# coding=utf-8
from __future__ import absolute_import
__author__ = "Florian Becker <florian@mr-beam.org> based on work by Gina Häußge and David Braam"
__license__ = "GNU Affero General Public License http://www.gnu.org/licenses/agpl.html"
__copyright__ = "Copyright (C) 2013 David Braam - Released under terms of the AGPLv3 License"

import os
import threading
import logging
import glob
import time
import serial
import re
import Queue

from yaml import load as yamlload
from yaml import dump as yamldump
from subprocess import call as subprocesscall

import octoprint.plugin

from octoprint.settings import settings, default_settings
from octoprint.events import eventManager, Events
from octoprint.filemanager.destinations import FileDestinations
from octoprint.util import get_exception_string, RepeatedTimer, CountedEvent, sanitize_ascii

### MachineCom #########################################################################################################
class MachineCom(object):
	STATE_NONE = 0
	STATE_OPEN_SERIAL = 1
	STATE_DETECT_SERIAL = 2
	STATE_CONNECTING = 3
	STATE_OPERATIONAL = 4
	STATE_PRINTING = 5
	STATE_PAUSED = 6
	STATE_CLOSED = 7
	STATE_ERROR = 8
	STATE_CLOSED_WITH_ERROR = 9
	STATE_LOCKED = 10
	STATE_HOMING = 11
	STATE_FLASHING = 12

	def __init__(self, port=None, baudrate=None, callbackObject=None, printerProfileManager=None):
		self._logger = logging.getLogger(__name__)
		self._serialLogger = logging.getLogger("SERIAL")

		if port is None:
			port = settings().get(["serial", "port"])
		elif isinstance(port, list):
			port = port[0]
		if baudrate is None:
			settingsBaudrate = settings().getInt(["serial", "baudrate"])
			if settingsBaudrate is None:
				baudrate = 0
			else:
				baudrate = settingsBaudrate
		if callbackObject is None:
			callbackObject = MachineComPrintCallback()

		self._port = port
		self._baudrate = baudrate
		self._callback = callbackObject
		self._printerProfileManager = printerProfileManager

		self.RX_BUFFER_SIZE = 127

		self._state = self.STATE_NONE
		self._grbl_state = None
		self._errorValue = "Unknown Error"
		self._serial = None
		self._currentFile = None
		self._status_timer = None
		self._acc_line_buffer = []
		self._cmd = None
		self._pauseWaitStartTime = None
		self._pauseWaitTimeLost = 0.0
		self._commandQueue = Queue.Queue()
		self._send_event = CountedEvent(max=50)
		self._finished_currentFile = False
		self._pause_delay_time = 0
		self._feedrate_factor = 1
		self._actual_feedrate = None
		self._intensity_factor = 1
		self._actual_intensity = None
		self._feedrate_dict = {}
		self._intensity_dict = {}

		# regular expressions
		self._regex_command = re.compile("^\s*\$?([GM]\d+|[TH])")
		self._regex_feedrate = re.compile("F\d+", re.IGNORECASE)
		self._regex_intensity = re.compile("S\d+", re.IGNORECASE)

		self._real_time_commands={'poll_status':False,
								'feed_hold':False,
								'cycle_start':False,
								'soft_reset':False}

		# metric stuff
		#self._metric_chars = 0
		#self._metric_time = None
		#self._metric_active = True
		#self._metric_thread = threading.Thread(target=self._metric_loop, name="comm._metric_thread")
		#self._metric_thread.daemon = True
		#self._metric_thread.start()

		# hooks
		self._pluginManager = octoprint.plugin.plugin_manager()
		self._serial_factory_hooks = self._pluginManager.get_hooks("octoprint.comm.transport.serial.factory")

		# monitoring thread
		self._monitoring_active = True
		self.monitoring_thread = threading.Thread(target=self._monitor_loop, name="comm._monitoring_thread")
		self.monitoring_thread.daemon = True
		self.monitoring_thread.start()

		# sending thread
		self._sending_active = True
		self.sending_thread = threading.Thread(target=self._send_loop, name="comm.sending_thread")
		self.sending_thread.daemon = True

	def _monitor_loop(self):
		#Open the serial port.
		if not self._openSerial():
			return

		self._log("Connected to: %s, starting monitor" % self._serial)
		self._changeState(self.STATE_CONNECTING)
		self._timeout = get_new_timeout("communication")

		while self._monitoring_active:
			try:
				line = self._readline()
				if line is None:
					break
				if line.strip() is not "":
					self._timeout = get_new_timeout("communication")
				if line.startswith('<'): # status report
					self._handle_status_report(line)
				elif line.startswith('ok'): # ok message :)
					self._handle_ok_message()
				elif line.startswith('err'): # error message
					self._handle_error_message(line)
				elif line.startswith('ALA'): # ALARM message
					self._handle_alarm_message(line)
				elif line.startswith('['): # feedback message
					self._handle_feedback_message(line)
				elif line.startswith('Grb'): # Grbl startup message
					self._handle_startup_message()
			except:
				self._logger.exception("Something crashed inside the monitoring loop, please report this to Mr. Beam")
				errorMsg = "See octoprint.log for details"
				self._log(errorMsg)
				self._errorValue = errorMsg
				self._changeState(self.STATE_ERROR)
				eventManager().fire(Events.ERROR, {"error": self.getErrorString()})
		self._log("Connection closed, closing down monitor")

	def _send_loop(self):
		while self._sending_active:
			try:
				self._process_rt_commands()
				if self.isPrinting() and self._commandQueue.empty():
					cmd = self._getNext()
					if cmd is not None:
						self.sendCommand(cmd)
						self._callback.on_comm_progress()
					elif len(self._acc_line_buffer) == 0:
						self._set_print_finished()

				self._sendCommand()
				self._send_event.wait(1)
				self._send_event.clear()
			except:
				self._logger.exception("Something crashed inside the sending loop, please report this to Mr. Beam")
				errorMsg = "See octoprint.log for details"
				self._log(errorMsg)
				self._errorValue = errorMsg
				self._changeState(self.STATE_ERROR)
				eventManager().fire(Events.ERROR, {"error": self.getErrorString()})

	def _metric_loop(self):
		self._metricf = open('metric.tmp','w')
		self._metricf.write("1 sec interval")
		while self._metric_active:
			time.sleep(1)
			if self._metric_time is not None:
				t = time.time()
				#s = "Metric: %f [chars/sec]" % (self._metric_chars / (t - self._metric_time))
				s = "%.2f" % (self._metric_chars / (t - self._metric_time))
				self._metric_time = None
				self._metric_chars = 0
				self._metricf.write(s)
				#self._log(s)
		self._metricf.close()

	def _sendCommand(self, cmd=None):
		if cmd is None:
			if self._cmd is None and self._commandQueue.empty():
				return
			elif self._cmd is None:
				self._cmd = self._commandQueue.get()
			if sum([len(x) for x in self._acc_line_buffer]) + len(self._cmd) +1 < self.RX_BUFFER_SIZE-5:
				self._cmd, _, _  = self._process_command_phase("sending", self._cmd)
				self._log("Send: %s" % self._cmd)
				self._acc_line_buffer.append(self._cmd + '\n')
				try:
					self._serial.write(self._cmd + '\n')
					self._process_command_phase("sent", self._cmd)
					self._cmd = None
					self._send_event.set()
				except serial.SerialException:
					self._log("Unexpected error while writing serial port: %s" % (get_exception_string()))
					self._errorValue = get_exception_string()
					self.close(True)
		else:
			cmd, _, _  = self._process_command_phase("sending", cmd)
			self._log("Send: %s" % cmd)
			try:

				self._serial.write(cmd)
				self._process_command_phase("sent", cmd)
			except serial.SerialException:
				self._log("Unexpected error while writing serial port: %s" % (get_exception_string()))
				self._errorValue = get_exception_string()
				self.close(True)

	def _process_rt_commands(self):
		if self._real_time_commands['poll_status']:
			self._sendCommand('?')
			self._real_time_commands['poll_status']=False
		elif self._real_time_commands['feed_hold']:
			self._sendCommand('!')
			self._real_time_commands['feed_hold']=False
		elif self._real_time_commands['cycle_start']:
			self._sendCommand('~')
			self._real_time_commands['cycle_start']=False
		elif self._real_time_commands['soft_reset']:
			self._sendCommand(b'\x18')
			self._real_time_commands['soft_reset']=False

	def _openSerial(self):
		def default(_, port, baudrate, read_timeout):
			if port is None or port == 'AUTO':
				# no known port, try auto detection
				self._changeState(self.STATE_DETECT_SERIAL)
				ser = self._detectPort(True)
				if ser is None:
					self._errorValue = 'Failed to autodetect serial port, please set it manually.'
					self._changeState(self.STATE_ERROR)
					eventManager().fire(Events.ERROR, {"error": self.getErrorString()})
					self._log("Failed to autodetect serial port, please set it manually.")
					return None
				port = ser.port

			# connect to regular serial port
			self._log("Connecting to: %s" % port)
			if baudrate == 0:
				baudrates = baudrateList()
				ser = serial.Serial(str(port), 115200 if 115200 in baudrates else baudrates[0], timeout=read_timeout, writeTimeout=10000, parity=serial.PARITY_ODD)
			else:
				ser = serial.Serial(str(port), baudrate, timeout=read_timeout, writeTimeout=10000, parity=serial.PARITY_ODD)
			ser.close()
			ser.parity = serial.PARITY_NONE
			ser.open()
			return ser

		serial_factories = self._serial_factory_hooks.items() + [("default", default)]
		for name, factory in serial_factories:
			try:
				serial_obj = factory(self, self._port, self._baudrate, settings().getFloat(["serial", "timeout", "connection"]))
			except (OSError, serial.SerialException):
				exception_string = get_exception_string()
				self._errorValue = "Connection error, see Terminal tab"
				self._changeState(self.STATE_ERROR)
				eventManager().fire(Events.ERROR, {"error": self.getErrorString()})
				self._log("Unexpected error while connecting to serial port: %s %s (hook %s)" % (self._port, exception_string, name))
				if "failed to set custom baud rate" in exception_string.lower():
					self._log("Your installation does not support custom baudrates (e.g. 250000) for connecting to your printer. This is a problem of the pyserial library that OctoPrint depends on. Please update to a pyserial version that supports your baudrate or switch your printer's firmware to a standard baudrate (e.g. 115200). See https://github.com/foosel/OctoPrint/wiki/OctoPrint-support-for-250000-baud-rate-on-Raspbian")
				return False
			if serial_obj is not None:
				# first hook to succeed wins, but any can pass on to the next
				self._log(repr(self._serial))
				self._changeState(self.STATE_OPEN_SERIAL)
				self._serial = serial_obj
				return True
		return False

	def _readline(self):
		if self._serial is None:
			return None
		try:
			ret = self._serial.readline()
			self._send_event.set()
			if('ok' in ret or 'error' in ret):
				if(len(self._acc_line_buffer) > 0):
					del self._acc_line_buffer[0]  # Delete the commands character count corresponding to the last 'ok'
		except serial.SerialException:
			self._log("Unexpected error while reading serial port: %s" % (get_exception_string()))
			self._errorValue = get_exception_string()
			self.close(True)
			return None
		if ret == '': return ''
		try:
			self._log("Recv: %s" % sanitize_ascii(ret))
		except ValueError as e:
			self._log("WARN: While reading last line: %s" % e)
			self._log("Recv: %r" % ret)
		return ret

	def _getNext(self):
		if self._finished_currentFile is False:
			line = self._currentFile.getNext()
			if line is None:
				self._finished_currentFile = True
			return line
		else:
			return None

	def _set_print_finished(self):
		self._callback.on_comm_print_job_done()
		self._changeState(self.STATE_OPERATIONAL)
		payload = {
			"file": self._currentFile.getFilename(),
			"filename": os.path.basename(self._currentFile.getFilename()),
			"origin": self._currentFile.getFileLocation(),
			"time": self.getPrintTime()
		}
		eventManager().fire(Events.PRINT_DONE, payload)
		self.sendCommand("M5")
		self.sendCommand("G0X0Y0")
		self.sendCommand("M9")

	def _handle_status_report(self, line):
		self._grbl_state = line[1:].split(',')[0]
		if self._grbl_state == 'Queue':
			if time.time() - self._pause_delay_time > 0.3:
				if not self.isPaused():
					self.setPause(True, False)
		elif self._grbl_state == 'Run' or self._grbl_state == 'Idle':
			if time.time() - self._pause_delay_time > 0.3:
				if self.isPaused():
					self.setPause(False, False)
		self._update_grbl_pos(line)
		#if self._metricf is not None:
		#	self._metricf.write(line)

	def _handle_ok_message(self):
		if self._state == self.STATE_HOMING:
			self._changeState(self.STATE_OPERATIONAL)

	def _handle_error_message(self, line):
		self._errorValue = line
		eventManager().fire(Events.ERROR, {"error": self.getErrorString()})
		self._changeState(self.STATE_LOCKED)

	def _handle_alarm_message(self, line):
		if "Hard/soft limit" in line:
			errorMsg = "Machine Limit Hit. Please reset the machine and do a homing cycle"
			self._log(errorMsg)
			self._errorValue = errorMsg
			eventManager().fire(Events.ERROR, {"error": self.getErrorString()})
			eventManager().fire(Events.LIMITS_HIT, {"error": self.getErrorString()})
		elif "Abort during cycle" in line:
			errorMsg = "Soft-reset detected. Please do a homing cycle"
			self._log(errorMsg)
			self._errorValue = errorMsg
			eventManager().fire(Events.ERROR, {"error": self.getErrorString()})
			eventManager().fire(Events.SOFT_RESET, {"error": self.getErrorString()})
		elif "Probe fail" in line:
			errorMsg = "Probing has failed. Please reset the machine and do a homing cycle"
			self._log(errorMsg)
			self._errorValue = errorMsg
			eventManager().fire(Events.ERROR, {"error": self.getErrorString()})

		with self._commandQueue.mutex:
			self._commandQueue.queue.clear()
		self._acc_line_buffer = []
		self._send_event.clear(completely=True)
		self._changeState(self.STATE_LOCKED)

		# close and open serial port to reset arduino
		self._serial.close()
		self._openSerial()

	def _handle_feedback_message(self, line):
		if line[1:].startswith('Res'): # [Reset to continue]
			pass
		elif line[1:].startswith('\'$H'): # ['$H'|'$X' to unlock]
			pass
		elif line[1:].startswith('Cau'): # [Caution: Unlocked]
			pass
		elif line[1:].startswith('Ena'): # [Enabled]
			pass
		elif line[1:].startswith('Dis'): # [Disabled]
			pass

	def _handle_startup_message(self):
		if self.isOperational():
			errorMsg = "Machine reset."
			self._cmd = None
			self._acc_line_buffer = []
			self._pauseWaitStartTime = None
			self._pauseWaitTimeLost = 0.0
			self._send_event.clear(completely=True)
			with self._commandQueue.mutex:
				self._commandQueue.queue.clear()
			self._log(errorMsg)
			self._errorValue = errorMsg
			self._changeState(self.STATE_LOCKED)
			eventManager().fire(Events.ERROR, {"error": self.getErrorString()})
		else:
			self._onConnected(self.STATE_LOCKED)

	def _update_grbl_pos(self, line):
		# line example:
		# <Idle,MPos:-434.000,-596.000,0.000,WPos:0.000,0.000,0.000,S:0,laser off:0>
		try:
			idx_mx_begin = line.index('MPos:') + 5
			idx_mx_end = line.index('.', idx_mx_begin) + 2
			idx_my_begin = line.index(',', idx_mx_end) + 1
			idx_my_end = line.index('.', idx_my_begin) + 2
			#idx_mz_begin = line.index(',', idx_my_end) + 1
			#idx_mz_end = line.index('.', idx_mz_begin) + 2

			idx_wx_begin = line.index('WPos:') + 5
			idx_wx_end = line.index('.', idx_wx_begin) + 2
			idx_wy_begin = line.index(',', idx_wx_end) + 1
			idx_wy_end = line.index('.', idx_wy_begin) + 2
			#idx_wz_begin = line.index(',', idx_wy_end) + 1
			#idx_wz_end = line.index('.', idx_wz_begin) + 2

			#idx_intensity_begin = line.index('S:', idx_wz_end) + 2
			#idx_intensity_end = line.index(',', idx_intensity_begin)

			#idx_laserstate_begin = line.index('laser ', idx_intensity_end) + 6
			#idx_laserstate_end = line.index(':', idx_laserstate_begin)

			#payload = {
			#"mx": line[idx_mx_begin:idx_mx_end],
			#"my": line[idx_my_begin:idx_my_end],
			#"mz": line[idx_mz_begin:idx_mz_end],
			#"wx": line[idx_wx_begin:idx_wx_end],
			#"wy": line[idx_wy_begin:idx_wy_end],
			#"wz": line[idx_wz_begin:idx_wz_end],
			#"laser": line[idx_laserstate_begin:idx_laserstate_end],
			#"intensity": line[idx_intensity_begin:idx_intensity_end]
			#}
			mx = float(line[idx_mx_begin:idx_mx_end])
			my = float(line[idx_my_begin:idx_my_end])
			wx = float(line[idx_wx_begin:idx_wx_end])
			wy = float(line[idx_wy_begin:idx_wy_end])
			self._callback.on_comm_pos_update([mx, my, 0], [wx, wy, 0])
		#eventManager().fire(Events.RT_STATE, payload)
		except ValueError:
			pass

	def _process_command_phase(self, phase, command, command_type=None, gcode=None):
		if phase not in ("queuing", "queued", "sending", "sent"):
			return command, command_type, gcode

		if gcode is None:
			gcode = self._gcode_command_for_cmd(command)

		# if it's a gcode command send it through the specific handler if it exists
		if gcode is not None:
			gcodeHandler = "_gcode_" + gcode + "_" + phase
			if hasattr(self, gcodeHandler):
				handler_result = getattr(self, gcodeHandler)(command, cmd_type=command_type)
				command, command_type, gcode = self._handle_command_handler_result(command, command_type, gcode, handler_result)

		# finally return whatever we resulted on
		return command, command_type, gcode

	def _gcode_command_for_cmd(self, cmd):
		"""
		Tries to parse the provided ``cmd`` and extract the GCODE command identifier from it (e.g. "G0" for "G0 X10.0").

		Arguments:
		    cmd (str): The command to try to parse.

		Returns:
		    str or None: The GCODE command identifier if it could be parsed, or None if not.
		"""
		if not cmd:
			return None

		if cmd == '!': return 'Hold'
		if cmd == '~': return 'Resume'

		gcode = self._regex_command.search(cmd)
		if not gcode:
			return None

		return gcode.group(1)

	# internal state management
	def _changeState(self, newState):
		if self._state == newState:
			return

		if newState == self.STATE_PRINTING:
			if self._status_timer is not None:
				self._status_timer.cancel()
			self._status_timer = RepeatedTimer(1, self._poll_status)
			self._status_timer.start()
		elif newState == self.STATE_OPERATIONAL:
			if self._status_timer is not None:
				self._status_timer.cancel()
			self._status_timer = RepeatedTimer(2, self._poll_status)
			self._status_timer.start()
		elif newState == self.STATE_PAUSED:
			if self._status_timer is not None:
				self._status_timer.cancel()
			self._status_timer = RepeatedTimer(0.2, self._poll_status)
			self._status_timer.start()

		if newState == self.STATE_CLOSED or newState == self.STATE_CLOSED_WITH_ERROR:
			if self._currentFile is not None:
				self._currentFile.close()
			self._log("entered state closed / closed with error. reseting character counter.")
			self.acc_line_lengths = []

		oldState = self.getStateString()
		self._state = newState
		self._log('Changing monitoring state from \'%s\' to \'%s\'' % (oldState, self.getStateString()))
		self._callback.on_comm_state_change(newState)

	def _onConnected(self, nextState):
		self._serial.timeout = settings().getFloat(["serial", "timeout", "communication"])

		if(nextState is None):
			self._changeState(self.STATE_LOCKED)
		else:
			self._changeState(nextState)

		if not self.sending_thread.isAlive():
			self.sending_thread.start()

		payload = dict(port=self._port, baudrate=self._baudrate)
		eventManager().fire(Events.CONNECTED, payload)

	def _detectPort(self, close):
		self._log("Serial port list: %s" % (str(serialList())))
		for p in serialList():
			try:
				self._log("Connecting to: %s" % (p))
				serial_obj = serial.Serial(p)
				if close:
					serial_obj.close()
				return serial_obj
			except (OSError, serial.SerialException) as e:
				self._log("Error while connecting to %s: %s" % (p, str(e)))
		return None

	def _poll_status(self):
		if self.isOperational():
			self._real_time_commands['poll_status']=True
			self._send_event.set()

	def _soft_reset(self):
		if self.isOperational():
			self._real_time_commands['soft_reset']=True
			self._send_event.set()

	def _log(self, message):
		self._callback.on_comm_log(message)
		self._serialLogger.debug(message)

	def _compareGrblVersion(self, versionDict):
		cwd = os.path.dirname(__file__)
		with open(cwd + "/../grbl/grblVersionRequirement.yml", 'r') as infile:
			grblReqDict = yamlload(infile)
		requiredGrblVer = str(grblReqDict['grbl']) + '_' + str(grblReqDict['git'])
		if grblReqDict['dirty'] is True:
			requiredGrblVer += '-dirty'
		actualGrblVer = str(versionDict['grbl']) + '_' + str(versionDict['git'])
		if versionDict['dirty'] is not(None):
			actualGrblVer += '-dirty'
		# compare actual and required grbl version
		self._requiredGrblVer = requiredGrblVer
		self._actualGrblVer = actualGrblVer
		print repr(requiredGrblVer)
		print repr(actualGrblVer)
		if requiredGrblVer != actualGrblVer:
			self._log("unsupported grbl version detected...")
			self._log("required: " + requiredGrblVer)
			self._log("detected: " + actualGrblVer)
			return False
		else:
			return True

	def _flashGrbl(self):
		self._changeState(self.STATE_FLASHING)
		self._serial.close()
		cwd = os.path.dirname(__file__)
		pathToGrblHex = cwd + "/../grbl/grbl.hex"

		# TODO check if avrdude is installed.
		# TODO log in logfile as well, not only to the serial monitor (use self._logger.info()... )
		params = ["avrdude", "-patmega328p", "-carduino", "-b" + str(self._baudrate), "-P" + str(self._port), "-D", "-Uflash:w:" + pathToGrblHex]
		rc = subprocesscall(params)

		if rc is False:
			self._log("successfully flashed new grbl version")
			self._openSerial()
			self._changeState(self.STATE_CONNECTING)
		else:
			self._log("error during flashing of new grbl version")
			self._errorValue = "avrdude returncode: %s" % rc
			self._changeState(self.STATE_CLOSED_WITH_ERROR)

	@staticmethod
	def _writeGrblVersionToFile(versionDict):
		if versionDict['dirty'] == '-dirty':
			versionDict['dirty'] = True
		versionDict['lastConnect'] = time.time()
		versionFile = os.path.join(settings().getBaseFolder("logs"), 'grbl_Version.yml')
		with open(versionFile, 'w') as outfile:
			outfile.write(yamldump(versionDict, default_flow_style=True))

	def _handle_command_handler_result(self, command, command_type, gcode, handler_result):
		original_tuple = (command, command_type, gcode)

		if handler_result is None:
			# handler didn't return anything, we'll just continue
			return original_tuple

		if isinstance(handler_result, basestring):
			# handler did return just a string, we'll turn that into a 1-tuple now
			handler_result = (handler_result,)
		elif not isinstance(handler_result, (tuple, list)):
			# handler didn't return an expected result format, we'll just ignore it and continue
			return original_tuple

		hook_result_length = len(handler_result)
		if hook_result_length == 1:
			# handler returned just the command
			command, = handler_result
		elif hook_result_length == 2:
			# handler returned command and command_type
			command, command_type = handler_result
		else:
			# handler returned a tuple of an unexpected length
			return original_tuple

		gcode = self._gcode_command_for_cmd(command)
		return command, command_type, gcode

	def _set_feedrate_override(self, value):
		temp = value / 100.0
		if temp > 0:
			self._feedrate_factor = temp
			self._feedrate_dict = {}
			if self._actual_feedrate is not None:
				temp = round(self._actual_feedrate * self._feedrate_factor)
				# TODO replace with value from printer profile
				if temp > 5000:
					temp = 5000
				self.sendCommand('F%d' % round(temp))

	def _set_intensity_override(self, value):
		temp = value / 100.0
		if temp >= 0:
			self._intensity_factor = temp
			self._intensity_dict = {}
			if self._actual_intensity is not None:
				temp = round(self._actual_intensity * self._intensity_factor)
				if temp > 1000:
					temp = 1000
				self.sendCommand('S%d' % round(temp))

	def _replace_feedrate(self, cmd):
		if self._feedrate_factor != 1:
			obj = self._regex_feedrate.search(cmd)
			if obj is not None:
				feedrate_cmd = cmd[obj.start():obj.end()]
				if feedrate_cmd in self._feedrate_dict:
					new_feedrate = self._feedrate_dict[feedrate_cmd]
				else:
					self._actual_feedrate = int(feedrate_cmd[1:])
					new_feedrate = round(self._actual_feedrate * self._feedrate_factor)
					# TODO replace with value from printer profile
					if new_feedrate > 5000:
						new_feedrate = 5000
					elif new_feedrate < 30:
						new_feedrate = 30
					self._feedrate_dict[feedrate_cmd] = new_feedrate
			else:
				return cmd
			return cmd.replace(feedrate_cmd, 'F%d' % round(new_feedrate))
		return cmd

	def _replace_intensity(self, cmd):
		if self._intensity_factor != 1:
			obj = self._regex_intensity.search(cmd)
			if obj is not None:
				intensity_cmd = cmd[obj.start():obj.end()]
				if intensity_cmd in self._intensity_dict:
					new_intensity = self._intensity_dict[intensity_cmd]
				else:
					self._actual_intensity = int(intensity_cmd[1:])
					new_intensity = round(self._actual_intensity * self._intensity_factor)
					if new_intensity > 1000:
						new_intensity = 1000
					self._intensity_dict[intensity_cmd] = new_intensity
			else:
				return cmd
			return cmd.replace(intensity_cmd, 'S%d' % round(new_intensity))
		return cmd

	##~~ command handlers
	def _gcode_G1_sending(self, cmd, cmd_type=None):
		cmd = self._replace_feedrate(cmd)
		cmd = self._replace_intensity(cmd)
		return cmd

	def _gcode_G2_sending(self, cmd, cmd_type=None):
		cmd = self._replace_feedrate(cmd)
		cmd = self._replace_intensity(cmd)
		return cmd

	def _gcode_G3_sending(self, cmd, cmd_type=None):
		cmd = self._replace_feedrate(cmd)
		cmd = self._replace_intensity(cmd)
		return cmd

	def _gcode_G01_sending(self, cmd, cmd_type=None):
		return self._gcode_G1_sending(cmd, cmd_type)

	def _gcode_G02_sending(self, cmd, cmd_type=None):
		return self._gcode_G2_sending(cmd, cmd_type)

	def _gcode_G03_sending(self, cmd, cmd_type=None):
		return self._gcode_G3_sending(cmd, cmd_type)

	def _gcode_H_sent(self, cmd, cmd_type=None):
		self._changeState(self.STATE_HOMING)
		return cmd

	def _gcode_Hold_sent(self, cmd, cmd_type=None):
		self._changeState(self.STATE_PAUSED)
		return cmd

	def _gcode_Resume_sent(self, cmd, cmd_type=None):
		self._changeState(self.STATE_PRINTING)
		return cmd

	def sendCommand(self, cmd, cmd_type=None, processed=False):
		cmd = cmd.encode('ascii', 'replace')
		if not processed:
			cmd = process_gcode_line(cmd)
			if not cmd:
				return

		if cmd[0] == "/":
			specialcmd = cmd[1:].lower()
			if "togglestatusreport" in specialcmd:
				if self._status_timer is None:
					self._status_timer = RepeatedTimer(1, self._poll_status)
					self._status_timer.start()
				else:
					self._status_timer.cancel()
					self._status_timer = None
			elif "setstatusfrequency" in specialcmd:
				data = specialcmd[18:]
				try:
					frequency = float(data)
				except ValueError:
					self._log("No frequency setting found! Using 1 sec.")
					frequency = 1
				if self._status_timer is not None:
					self._status_timer.cancel()
				self._status_timer = RepeatedTimer(frequency, self._poll_status)
				self._status_timer.start()
			elif "disconnect" in specialcmd:
				self.close()
			elif "feedrate" in specialcmd:
				data = specialcmd[8:]
				self._set_feedrate_override(int(data))
			elif "intensity" in specialcmd:
				data = specialcmd[9:]
				self._set_intensity_override(int(data))
			else:
				self._log("Command not Found! %s" % cmd)
				self._log("available commands are:")
				self._log("   /togglestatusreport")
				self._log("   /setstatusfrequency <Inteval Sec>")
				self._log("   /feedrate <%>")
				self._log("   /intensity <%>")
				self._log("   /disconnect")
			return

		eepromCmd = re.search("^\$[0-9]+=.+$", cmd)
		if(eepromCmd and self.isPrinting()):
			self._log("Warning: Configuration changes during print are not allowed!")

		self._commandQueue.put(cmd)
		self._send_event.set()

	def selectFile(self, filename, sd):
		if self.isBusy():
			return

		self._currentFile = PrintingGcodeFileInformation(filename)
		eventManager().fire(Events.FILE_SELECTED, {
			"file": self._currentFile.getFilename(),
			"filename": os.path.basename(self._currentFile.getFilename()),
			"origin": self._currentFile.getFileLocation()
		})
		self._callback.on_comm_file_selected(filename, self._currentFile.getFilesize(), False)

	def unselectFile(self):
		if self.isBusy():
			return

		self._currentFile = None
		eventManager().fire(Events.FILE_DESELECTED)
		self._callback.on_comm_file_selected(None, None, False)

	def startPrint(self):
		if not self.isOperational():
			return

		if self._currentFile is None:
			raise ValueError("No file selected for printing")

		try:
			# ensure fan is on whatever gcode follows.
			self.sendCommand("M08")

			self._currentFile.start()
			self._finished_currentFile = False

			payload = {
				"file": self._currentFile.getFilename(),
				"filename": os.path.basename(self._currentFile.getFilename()),
				"origin": self._currentFile.getFileLocation()
			}
			eventManager().fire(Events.PRINT_STARTED, payload)
			#self.sendGcodeScript("beforePrintStarted", replacements=dict(event=payload))

			self._changeState(self.STATE_PRINTING)
		except:
			self._logger.exception("Error while trying to start printing")
			self._errorValue = get_exception_string()
			self._changeState(self.STATE_ERROR)
			eventManager().fire(Events.ERROR, {"error": self.getErrorString()})

	def cancelPrint(self):
		if not self.isOperational():
			return

		with self._commandQueue.mutex:
			self._commandQueue.queue.clear()
		self._soft_reset()
		self._acc_line_buffer = []
		self._send_event.clear(completely=True)
		self._changeState(self.STATE_LOCKED)

		payload = {
			"file": self._currentFile.getFilename(),
			"filename": os.path.basename(self._currentFile.getFilename()),
			"origin": self._currentFile.getFileLocation()
		}

		eventManager().fire(Events.PRINT_CANCELLED, payload)

	def setPause(self, pause, send_cmd=True):
		if not self._currentFile:
			return

		payload = {
			"file": self._currentFile.getFilename(),
			"filename": os.path.basename(self._currentFile.getFilename()),
			"origin": self._currentFile.getFileLocation()
		}

		if not pause and self.isPaused():
			if self._pauseWaitStartTime:
				self._pauseWaitTimeLost = self._pauseWaitTimeLost + (time.time() - self._pauseWaitStartTime)
				self._pauseWaitStartTime = None
			self._pause_delay_time = time.time()
			if send_cmd is True:
				self._real_time_commands['cycle_start']=True
			self._send_event.set()
			eventManager().fire(Events.PRINT_RESUMED, payload)
		elif pause and self.isPrinting():
			if not self._pauseWaitStartTime:
				self._pauseWaitStartTime = time.time()
			self._pause_delay_time = time.time()
			if send_cmd is True:
				self._real_time_commands['feed_hold']=True
			self._send_event.set()
			eventManager().fire(Events.PRINT_PAUSED, payload)

	def getStateString(self):
		if self._state == self.STATE_NONE:
			return "Offline"
		if self._state == self.STATE_OPEN_SERIAL:
			return "Opening serial port"
		if self._state == self.STATE_DETECT_SERIAL:
			return "Detecting serial port"
		if self._state == self.STATE_CONNECTING:
			return "Connecting"
		if self._state == self.STATE_OPERATIONAL:
			return "Operational"
		if self._state == self.STATE_PRINTING:
			return "Printing"
		if self._state == self.STATE_PAUSED:
			return "Paused"
		if self._state == self.STATE_CLOSED:
			return "Closed"
		if self._state == self.STATE_ERROR:
			return "Error: %s" % (self.getErrorString())
		if self._state == self.STATE_CLOSED_WITH_ERROR:
			return "Error: %s" % (self.getErrorString())
		if self._state == self.STATE_LOCKED:
			return "Locked"
		if self._state == self.STATE_HOMING:
			return "Homing"
		if self._state == self.STATE_FLASHING:
			return "Flashing"
		return "?%d?" % (self._state)

	def getPrintProgress(self):
		if self._currentFile is None:
			return None
		return self._currentFile.getProgress()

	def getPrintFilepos(self):
		if self._currentFile is None:
			return None
		return self._currentFile.getFilepos()

	def getCleanedPrintTime(self):
		printTime = self.getPrintTime()
		if printTime is None:
			return None
		return printTime

	def getConnection(self):
		return self._port, self._baudrate

	def isOperational(self):
		return self._state == self.STATE_OPERATIONAL or self._state == self.STATE_PRINTING or self._state == self.STATE_PAUSED

	def isPrinting(self):
		return self._state == self.STATE_PRINTING

	def isPaused(self):
		return self._state == self.STATE_PAUSED

	def isLocked(self):
		return self._state == self.STATE_LOCKED

	def isHoming(self):
		return self._state == self.STATE_HOMING

	def isFlashing(self):
		return self._state == self.STATE_FLASHING

	def isBusy(self):
		return self.isPrinting() or self.isPaused()

	def isError(self):
		return self._state == self.STATE_ERROR or self._state == self.STATE_CLOSED_WITH_ERROR

	def isClosedOrError(self):
		return self._state == self.STATE_ERROR or self._state == self.STATE_CLOSED_WITH_ERROR or self._state == self.STATE_CLOSED

	def isSdReady(self):
		return False

	def isStreaming(self):
		return False

	def getErrorString(self):
		return self._errorValue

	def getPrintTime(self):
		if self._currentFile is None or self._currentFile.getStartTime() is None:
			return None
		else:
			return time.time() - self._currentFile.getStartTime() - self._pauseWaitTimeLost

	def close(self, isError = False):
		if self._status_timer is not None:
			try:
				self._status_timer.cancel()
				self._status_timer = None
			except AttributeError:
				pass

		self._monitoring_active = False
		self._sending_active = False

		printing = self.isPrinting() or self.isPaused()
		if self._serial is not None:
			if isError:
				self._changeState(self.STATE_CLOSED_WITH_ERROR)
			else:
				self._changeState(self.STATE_CLOSED)
			self._serial.close()
		self._serial = None

		if printing:
			payload = None
			if self._currentFile is not None:
				payload = {
					"file": self._currentFile.getFilename(),
					"filename": os.path.basename(self._currentFile.getFilename()),
					"origin": self._currentFile.getFileLocation()
				}
			eventManager().fire(Events.PRINT_FAILED, payload)
		eventManager().fire(Events.DISCONNECTED)

### MachineCom callback ################################################################################################
class MachineComPrintCallback(object):
	def on_comm_log(self, message):
		pass

	def on_comm_temperature_update(self, temp, bedTemp):
		pass

	def on_comm_state_change(self, state):
		pass

	def on_comm_message(self, message):
		pass

	def on_comm_progress(self):
		pass

	def on_comm_print_job_done(self):
		pass

	def on_comm_z_change(self, newZ):
		pass

	def on_comm_file_selected(self, filename, filesize, sd):
		pass

	def on_comm_sd_state_change(self, sdReady):
		pass

	def on_comm_sd_files(self, files):
		pass

	def on_comm_file_transfer_started(self, filename, filesize):
		pass

	def on_comm_file_transfer_done(self, filename):
		pass

	def on_comm_force_disconnect(self):
		pass

	def on_comm_pos_update(self, MPos, WPos):
		pass

class PrintingFileInformation(object):
	"""
	Encapsulates information regarding the current file being printed: file name, current position, total size and
	time the print started.
	Allows to reset the current file position to 0 and to calculate the current progress as a floating point
	value between 0 and 1.
	"""

	def __init__(self, filename):
		self._logger = logging.getLogger(__name__)
		self._filename = filename
		self._pos = 0
		self._size = None
		self._start_time = None

	def getStartTime(self):
		return self._start_time

	def getFilename(self):
		return self._filename

	def getFilesize(self):
		return self._size

	def getFilepos(self):
		return self._pos

	def getFileLocation(self):
		return FileDestinations.LOCAL

	def getProgress(self):
		"""
		The current progress of the file, calculated as relation between file position and absolute size. Returns -1
		if file size is None or < 1.
		"""
		if self._size is None or not self._size > 0:
			return -1
		return float(self._pos) / float(self._size)

	def reset(self):
		"""
		Resets the current file position to 0.
		"""
		self._pos = 0

	def start(self):
		"""
		Marks the print job as started and remembers the start time.
		"""
		self._start_time = time.time()

	def close(self):
		"""
		Closes the print job.
		"""
		pass

class PrintingGcodeFileInformation(PrintingFileInformation):
	"""
	Encapsulates information regarding an ongoing direct print. Takes care of the needed file handle and ensures
	that the file is closed in case of an error.
	"""

	def __init__(self, filename, offsets_callback=None, current_tool_callback=None):
		PrintingFileInformation.__init__(self, filename)

		self._handle = None

		self._first_line = None

		self._offsets_callback = offsets_callback
		self._current_tool_callback = current_tool_callback

		if not os.path.exists(self._filename) or not os.path.isfile(self._filename):
			raise IOError("File %s does not exist" % self._filename)
		self._size = os.stat(self._filename).st_size
		self._pos = 0

	def start(self):
		"""
		Opens the file for reading and determines the file size.
		"""
		PrintingFileInformation.start(self)
		self._handle = open(self._filename, "r")

	def close(self):
		"""
		Closes the file if it's still open.
		"""
		PrintingFileInformation.close(self)
		if self._handle is not None:
			try:
				self._handle.close()
			except:
				pass
		self._handle = None

	def getNext(self):
		"""
		Retrieves the next line for printing.
		"""
		if self._handle is None:
			raise ValueError("File %s is not open for reading" % self._filename)

		try:
			processed = None
			while processed is None:
				if self._handle is None:
					# file got closed just now
					return None
				line = self._handle.readline()
				if not line:
					self.close()
				processed = process_gcode_line(line)
			self._pos = self._handle.tell()

			return processed
		except Exception as e:
			self.close()
			self._logger.exception("Exception while processing line")
			raise e

def convert_pause_triggers(configured_triggers):
	triggers = {
		"enable": [],
		"disable": [],
		"toggle": []
	}
	for trigger in configured_triggers:
		if not "regex" in trigger or not "type" in trigger:
			continue

		try:
			regex = trigger["regex"]
			t = trigger["type"]
			if t in triggers:
				# make sure regex is valid
				re.compile(regex)
				# add to type list
				triggers[t].append(regex)
		except re.error:
			# invalid regex or something like this, we'll just skip this entry
			pass

	result = dict()
	for t in triggers.keys():
		if len(triggers[t]) > 0:
			result[t] = re.compile("|".join(map(lambda pattern: "({pattern})".format(pattern=pattern), triggers[t])))
	return result

def process_gcode_line(line):
	line = strip_comment(line).strip()
	line = line.replace(" ", "")
	if not len(line):
		return None
	return line

def strip_comment(line):
	if not ";" in line:
		# shortcut
		return line

	escaped = False
	result = []
	for c in line:
		if c == ";" and not escaped:
			break
		result += c
		escaped = (c == "\\") and not escaped
	return "".join(result)

def get_new_timeout(t):
	now = time.time()
	return now + get_interval(t)

def get_interval(t):
	if t not in default_settings["serial"]["timeout"]:
		return 0
	else:
		return settings().getFloat(["serial", "timeout", t])

def serialList():
	baselist = []
	baselist = baselist \
				+ glob.glob("/dev/ttyUSB*") \
				+ glob.glob("/dev/ttyACM*") \
				+ glob.glob("/dev/tty.usb*") \
				+ glob.glob("/dev/cu.*") \
				+ glob.glob("/dev/cuaU*") \
				+ glob.glob("/dev/rfcomm*")

	additionalPorts = settings().get(["serial", "additionalPorts"])
	for additional in additionalPorts:
		baselist += glob.glob(additional)

	prev = settings().get(["serial", "port"])
	if prev in baselist:
		baselist.remove(prev)
		baselist.insert(0, prev)
	if settings().getBoolean(["devel", "virtualPrinter", "enabled"]):
		baselist.append("VIRTUAL")
	return filter(None, baselist)

def baudrateList():
	ret = [250000, 230400, 115200, 57600, 38400, 19200, 9600]
	prev = settings().getInt(["serial", "baudrate"])
	if prev in ret:
		ret.remove(prev)
		ret.insert(0, prev)
	return ret
