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

from yaml import load as yamlload
from yaml import dump as yamldump
from subprocess import call as subprocesscall

import octoprint.plugin

from octoprint.events import eventManager, Events
from octoprint.settings import settings, default_settings
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
		self._errorValue = "Unknown Error"
		self._serial = None
		self._currentFile = None
		self._status_timer = None
		self._acc_line_buffer = []
		self._pauseWaitTimeLost = 0.0
		self._send_event = threading.Event()
		self._send_event.clear()

		self._real_time_commands={'poll_status':False,
								'feed_hold':False,
								'cycle_start':False,
								'soft_reset':False}

		# hooks
		self._pluginManager = octoprint.plugin.plugin_manager()
		self._serial_factory_hooks = self._pluginManager.get_hooks("octoprint.comm.transport.serial.factory")

		self._state_parse_dict = {self.STATE_NONE:self._state_none_handle,
								self.STATE_CONNECTING:self._state_connecting_handle,
								self.STATE_LOCKED:self._state_locked_handle,
								self.STATE_HOMING:self._state_homing_handle,
								self.STATE_OPERATIONAL:self._state_operational_handle}

		# monitoring thread
		self._monitoring_active = True
		self.monitoring_thread = threading.Thread(target=self._monitor_loop, name="comm._monitoring_thread")
		self.monitoring_thread.daemon = True
		self.monitoring_thread.start()

		# sending thread
		self._sending_active = True
		self.sending_thread = threading.Thread(target=self._send_loop, name="comm.sending_thread")
		self.sending_thread.daemon = True
		self.sending_thread.start()

	def _monitor_loop(self):
		pause_triggers = convert_pause_triggers(settings().get(["printerParameters", "pauseTriggers"]))

		#Open the serial port.
		if not self._openSerial():
			return

		self._log("Connected to: %s, starting monitor" % self._serial)
		self._changeState(self.STATE_CONNECTING)
		self._timeout = get_new_timeout("communication")

		supportWait = settings().getBoolean(["feature", "supportWait"])

		while self._monitoring_active:
			try:
				line = self._readline()
				if line is None:
					break
				if line.strip() is not "":
					self._timeout = get_new_timeout("communication")
				# parse line depending on state
				self._state_parse_dict[self._state](self, line)
				return
			except:
				self._logger.exception("Something crashed inside the monitoring loop, please report this to Mr. Beam")
				errorMsg = "See octoprint.log for details"
				self._log(errorMsg)
				self._errorValue = errorMsg
				self._changeState(self.STATE_ERROR)
				eventManager().fire(Events.ERROR, {"error": self.getErrorString()})
		self._log("Connection closed, closing down monitor")

	def _send_loop(self):
		# first wait until serial connection is established
		self._send_event.wait()
		self._send_event.clear()

		while self._sending_active:
			try:
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
				elif self.isOperational() or self.isPaused():
					pass # TODO send buffered command
				elif self.isPrinting():
					self._sendCommand(self._getNext())
				self._send_event.wait(1)
				self._send_event.clear()
			except:
				self._logger.exception("Something crashed inside the sending loop, please report this to Mr. Beam")
				errorMsg = "See octoprint.log for details"
				self._log(errorMsg)
				self._errorValue = errorMsg
				self._changeState(self.STATE_ERROR)
				eventManager().fire(Events.ERROR, {"error": self.getErrorString()})

	def _sendCommand(self, cmd):
		if sum([len(x) for x in self._acc_line_buffer]) + len(cmd) +1 < self.RX_BUFFER_SIZE:
			self._log("Send: %s" % cmd)
			self._acc_line_buffer.append(cmd)
			try:
				self._serial.write(cmd + '\n')
			except serial.SerialException:
				self._log("Unexpected error while writing serial port: %s" % (get_exception_string()))
				self._errorValue = get_exception_string()
				self.close(True)

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
				self._changeState(self.STATE_OPEN_SERIAL)
				self._log(repr(self._serial))
				self._serial = serial_obj
				return True
		return False

	def _readline(self):
		if self._serial is None:
			return None
		try:
			ret = self._serial.readline()
			if('ok' in ret or 'error' in ret):
				if(len(self.acc_line_lengths) > 0):
					#print('buffer',sum(self.acc_line_lengths), 'deleting after ok', self.acc_line_lengths[0])
					del self.acc_line_lengths[0]  # Delete the commands character count corresponding to the last 'ok'
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
		line = self._currentFile.getNext()
		if line is None:
			payload = {
				"file": self._currentFile.getFilename(),
				"filename": os.path.basename(self._currentFile.getFilename()),
				"origin": self._currentFile.getFileLocation(),
				"time": self.getPrintTime()
			}
			self._callback.on_comm_print_job_done()
			self._changeState(self.STATE_OPERATIONAL)
			eventManager().fire(Events.PRINT_DONE, payload)

			self.sendCommand("M5")
			self.sendCommand("G0X0Y0")
			self.sendCommand("M9")
		return line

	def _state_none_handle(self, line):
		pass

	def _state_connecting_handle(self, line):
		if line.startswith("Grbl"):
			versionMatch = re.search("Grbl (?P<grbl>.+?)(_(?P<git>[0-9a-f]{7})(?P<dirty>-dirty)?)? \[.+\]", line)
			if(versionMatch):
				versionDict = versionMatch.groupdict()
				self._writeGrblVersionToFile(versionDict)
				if self._compareGrblVersion(versionDict) is False:
					self._flashGrbl()
			self._send_event.set()
			self._onConnected(self.STATE_LOCKED)

	def _state_locked_handle(self, line):
		pass

	def _state_homing_handle(self, line):
		if line.startswith("ok"):
			self._changeState(self.STATE_OPERATIONAL)

	def _state_operational_handle(self, line):
		pass

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

	def sendCommand(self, cmd, cmd_type=None, processed=False):
		cmd = cmd.encode('ascii', 'replace')
		if not processed:
			cmd = process_gcode_line(cmd)
			if not cmd:
				return

		# if cmd[0] == "/":
		# 	specialcmd = cmd[1:].lower()
		# 	if "togglestatusreport" in specialcmd:
		# 		if self._temperature_timer is None:
		# 			self._temperature_timer = RepeatedTimer(1, self._poll_temperature, run_first=True)
		# 			self._temperature_timer.start()
		# 		else:
		# 			self._temperature_timer.cancel()
		# 			self._temperature_timer = None
		# 	elif "setstatusfrequency" in specialcmd:
		# 		data = specialcmd.split(' ')
		# 		try:
		# 			frequency = float(data[1])
		# 		except ValueError:
		# 			self._log("No frequency setting found! Using 1 sec.")
		# 			frequency = 1
		# 		if self._temperature_timer is not None:
		# 			self._temperature_timer.cancel()
		#
		# 		self._temperature_timer = RepeatedTimer(frequency, self._poll_temperature, run_first=True)
		# 		self._temperature_timer.start()
		# 	elif "disconnect" in specialcmd:
		# 		self.close()
		# 	else:
		# 		self._log("Command not Found! %s" % cmd)
		# 		self._log("available commands are:")
		# 		self._log("   /togglestatusreport")
		# 		self._log("   /setstatusfrequency <Inteval Sec>")
		# 		self._log("   /disconnect")
		# 	return

		eepromCmd = re.search("^\$[0-9]+=.+$", cmd)
		if(eepromCmd and self.isPrinting()):
			self._log("Warning: Configuration changes during print are not allowed!")

		if self.isPrinting():
			self._commandQueue.put((cmd, cmd_type))
		elif self.isOperational() or self.isLocked() or self.isHoming():
			self._sendCommand(cmd, cmd_type=cmd_type)

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

	def isBusy(self):
		return self.isPrinting() or self.isPaused()

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

		self.sending_thread.join()
		self.monitoring_thread.join()

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
	if len(line):
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
		return settings().getFloat(["serial", "timeout", type])

def serialList():
	baselist = [glob.glob("/dev/ttyUSB*"),
				glob.glob("/dev/ttyACM*"),
				glob.glob("/dev/tty.usb*"),
				glob.glob("/dev/cu.*"),
				glob.glob("/dev/cuaU*"),
				glob.glob("/dev/rfcomm*")]

	additionalPorts = settings().get(["serial", "additionalPorts"])
	for additional in additionalPorts:
		baselist += glob.glob(additional)

	prev = settings().get(["serial", "port"])
	if prev in baselist:
		baselist.remove(prev)
		baselist.insert(0, prev)
	if settings().getBoolean(["devel", "virtualPrinter", "enabled"]):
		baselist.append("VIRTUAL")
	return baselist

def baudrateList():
	ret = [250000, 230400, 115200, 57600, 38400, 19200, 9600]
	prev = settings().getInt(["serial", "baudrate"])
	if prev in ret:
		ret.remove(prev)
		ret.insert(0, prev)
	return ret
