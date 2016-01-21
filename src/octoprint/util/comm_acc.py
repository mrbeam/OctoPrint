# coding=utf-8
from __future__ import absolute_import
__author__ = "Gina Häußge <osd@foosel.net> based on work by David Braam"
__license__ = "GNU Affero General Public License http://www.gnu.org/licenses/agpl.html"
__copyright__ = "Copyright (C) 2013 David Braam - Released under terms of the AGPLv3 License"

import os
import glob
import time
import re
import threading
import Queue as queue
import logging
import serial
import octoprint.plugin

from collections import deque

from octoprint.util.avr_isp import stk500v2
from octoprint.util.avr_isp import ispBase

from octoprint.settings import settings, default_settings
from octoprint.events import eventManager, Events
from octoprint.filemanager import valid_file_type
from octoprint.filemanager.destinations import FileDestinations
from octoprint.util import get_exception_string, sanitize_ascii, filter_non_ascii, CountedEvent, RepeatedTimer

try:
	import _winreg
except:
	pass

def serialList():
	baselist=[]
	if os.name=="nt":
		try:
			key=_winreg.OpenKey(_winreg.HKEY_LOCAL_MACHINE,"HARDWARE\\DEVICEMAP\\SERIALCOMM")
			i=0
			while(1):
				baselist+=[_winreg.EnumValue(key,i)[1]]
				i+=1
		except:
			pass
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
	return baselist

def baudrateList():
	ret = [250000, 230400, 115200, 57600, 38400, 19200, 9600]
	prev = settings().getInt(["serial", "baudrate"])
	if prev in ret:
		ret.remove(prev)
		ret.insert(0, prev)
	return ret

gcodeToEvent = {
	# pause for user input
	"M226": Events.WAITING,
	"M0": Events.WAITING,
	"M1": Events.WAITING,
	# dwell command
	"G4": Events.DWELL,

	# part cooler
	"M245": Events.COOLING,

	# part conveyor
	"M240": Events.CONVEYOR,

	# part ejector
	"M40": Events.EJECT,

	# user alert
	"M300": Events.ALERT,

	# home print head
	"$H": Events.HOME,

	# emergency stop
	"M112": Events.E_STOP,

	# motors on/off
	"M80": Events.POWER_ON,
	"M81": Events.POWER_OFF,
}

class MachineCom(object):
	STATE_NONE = 0
	STATE_OPEN_SERIAL = 1
	STATE_DETECT_SERIAL = 2
	STATE_DETECT_BAUDRATE = 3
	STATE_CONNECTING = 4
	STATE_OPERATIONAL = 5
	STATE_PRINTING = 6
	STATE_PAUSED = 7
	STATE_CLOSED = 8
	STATE_ERROR = 9
	STATE_CLOSED_WITH_ERROR = 10
	STATE_TRANSFERING_FILE = 11
	STATE_LOCKED = 12
	STATE_HOMING = 13
	STATE_FLASHING = 14



	def __init__(self, port = None, baudrate=None, callbackObject=None, printerProfileManager=None):
		self._logger = logging.getLogger(__name__)
		self._serialLogger = logging.getLogger("SERIAL")

		if port == None:
			port = settings().get(["serial", "port"])
		if baudrate == None:
			settingsBaudrate = settings().getInt(["serial", "baudrate"])
			if settingsBaudrate is None:
				baudrate = 0
			else:
				baudrate = settingsBaudrate
		if callbackObject == None:
			callbackObject = MachineComPrintCallback()

		self._port = port
		self._baudrate = baudrate
		self._callback = callbackObject
		self._printerProfileManager = printerProfileManager
		self._state = self.STATE_NONE
		self._serial = None
		self._baudrateDetectList = baudrateList()
		self._baudrateDetectRetry = 0
		self._temp = {}
		self._bedTemp = None
		self._tempOffsets = dict()
		self._commandQueue = queue.Queue()
		self._currentZ = None
		self._heatupWaitStartTime = None
		self._heatupWaitTimeLost = 0.0
		self._pauseWaitStartTime = None
		self._pauseWaitTimeLost = 0.0
		self._currentTool = 0

		self._long_running_command = False
		self._heating = False

		self._timeout = None

		self._errorValue = "Unknown Error"

		self._alwaysSendChecksum = settings().getBoolean(["feature", "alwaysSendChecksum"])
		self._sendChecksumWithUnknownCommands = settings().getBoolean(["feature", "sendChecksumWithUnknownCommands"])
		self._unknownCommandsNeedAck = settings().getBoolean(["feature", "unknownCommandsNeedAck"])
		self._currentLine = 1
		self._resendDelta = None
		self._lastLines = deque([], 50)
		self._lastCommError = None
		self._lastResendNumber = None
		self._currentResendCount = 0
		self._resendSwallowNextOk = False

		self.RX_BUFFER_SIZE = 127 # size of the Arduino RX Buffer. TODO: put in machine profile
		self.acc_line_lengths = [] # store char count of all send commands until ok is received
		self._clear_to_send = CountedEvent(max=10, name="comm.clear_to_send")
		self._send_queue = TypedQueue()
		self._temperature_timer = None
		self._sd_status_timer = None

		# enabled grbl mode if requested
		self._grbl = settings().getBoolean(["feature", "grbl"])

		# hooks
		self._pluginManager = octoprint.plugin.plugin_manager()

		self._gcode_hooks = dict(
			queuing=self._pluginManager.get_hooks("octoprint.comm.protocol.gcode.queuing"),
			queued=self._pluginManager.get_hooks("octoprint.comm.protocol.gcode.queued"),
			sending=self._pluginManager.get_hooks("octoprint.comm.protocol.gcode.sending"),
			sent=self._pluginManager.get_hooks("octoprint.comm.protocol.gcode.sent")
		)

		self._printer_action_hooks = self._pluginManager.get_hooks("octoprint.comm.protocol.action")
		self._gcodescript_hooks = self._pluginManager.get_hooks("octoprint.comm.protocol.scripts")
		self._serial_factory_hooks = self._pluginManager.get_hooks("octoprint.comm.transport.serial.factory")

		# SD status data
		self._sdAvailable = False
		self._sdFileList = False
		self._sdFiles = []
		self._sdFileToSelect = None
		self._ignore_select = False

		# print job
		self._currentFile = None

		# regexes
		floatPattern = "[-+]?[0-9]*\.?[0-9]+"
		positiveFloatPattern = "[+]?[0-9]*\.?[0-9]+"
		intPattern = "\d+"
		self._regex_command = re.compile("^\s*\$?([GM]\d+|[TH])")
		self._regex_float = re.compile(floatPattern)
		self._regex_paramZFloat = re.compile("Z(%s)" % floatPattern)
		self._regex_paramSInt = re.compile("S(%s)" % intPattern)
		self._regex_paramNInt = re.compile("N(%s)" % intPattern)
		self._regex_paramTInt = re.compile("T(%s)" % intPattern)
		self._regex_minMaxError = re.compile("Error:[0-9]\n")
		self._regex_sdPrintingByte = re.compile("([0-9]*)/([0-9]*)")
		self._regex_sdFileOpened = re.compile("File opened:\s*(.*?)\s+Size:\s*(%s)" % intPattern)

		# Regex matching temperature entries in line. Groups will be as follows:
		# - 1: whole tool designator incl. optional toolNumber ("T", "Tn", "B")
		# - 2: toolNumber, if given ("", "n", "")
		# - 3: actual temperature
		# - 4: whole target substring, if given (e.g. " / 22.0")
		# - 5: target temperature
		self._regex_temp = re.compile("(B|T(\d*)):\s*(%s)(\s*\/?\s*(%s))?" % (positiveFloatPattern, positiveFloatPattern))
		self._regex_repetierTempExtr = re.compile("TargetExtr([0-9]+):(%s)" % positiveFloatPattern)
		self._regex_repetierTempBed = re.compile("TargetBed:(%s)" % positiveFloatPattern)

		self._long_running_commands = settings().get(["serial", "longRunningCommands"])

		# multithreading locks
		self._sendNextLock = threading.Lock()
		self._sendingLock = threading.RLock()

		# monitoring thread
		self._monitoring_active = True
		self.monitoring_thread = threading.Thread(target=self._monitor, name="comm._monitor")
		self.monitoring_thread.daemon = True
		self.monitoring_thread.start()

		# sending thread
		self._send_queue_active = True
		self.sending_thread = threading.Thread(target=self._send_loop, name="comm.sending_thread")
		self.sending_thread.daemon = True
		self.sending_thread.start()

	def __del__(self):
		self.close()

	##~~ internal state management

	def _changeState(self, newState):
		if self._state == newState:
			return

		if newState == self.STATE_PRINTING:
			if self._temperature_timer is not None:
				self._temperature_timer.cancel()
		elif newState == self.STATE_OPERATIONAL:
			if self._temperature_timer is not None:
				self._temperature_timer.cancel()
			self._temperature_timer = RepeatedTimer(1, self._poll_temperature, run_first=True)
			self._temperature_timer.start()

		if newState == self.STATE_CLOSED or newState == self.STATE_CLOSED_WITH_ERROR:
			if settings().get(["feature", "sdSupport"]):
				self._sdFileList = False
				self._sdFiles = []
				self._callback.on_comm_sd_files([])

			if self._currentFile is not None:
				self._currentFile.close()
			self._log("entered state closed / closed with error. reseting character counter.")
			self.acc_line_lengths = []

		oldState = self.getStateString()
		self._state = newState
		self._log('Changing monitoring state from \'%s\' to \'%s\'' % (oldState, self.getStateString()))
		self._callback.on_comm_state_change(newState)

	def _log(self, message):
		self._callback.on_comm_log(message)
		self._serialLogger.debug(message)

	def _addToLastLines(self, cmd):
		self._lastLines.append(cmd)

	##~~ getters

	def getState(self):
		return self._state

	def getStateString(self):
		if self._state == self.STATE_NONE:
			return "Offline"
		if self._state == self.STATE_OPEN_SERIAL:
			return "Opening serial port"
		if self._state == self.STATE_DETECT_SERIAL:
			return "Detecting serial port"
		if self._state == self.STATE_DETECT_BAUDRATE:
			return "Detecting baudrate"
		if self._state == self.STATE_CONNECTING:
			return "Connecting"
		if self._state == self.STATE_OPERATIONAL:
			return "Operational"
		if self._state == self.STATE_PRINTING:
			if self.isSdFileSelected():
				return "Printing from SD"
			elif self.isStreaming():
				return "Sending file to SD"
			else:
				return "Printing"
		if self._state == self.STATE_PAUSED:
			return "Paused"
		if self._state == self.STATE_CLOSED:
			return "Closed"
		if self._state == self.STATE_ERROR:
			return "Error: %s" % (self.getErrorString())
		if self._state == self.STATE_CLOSED_WITH_ERROR:
			return "Error: %s" % (self.getErrorString())
		if self._state == self.STATE_TRANSFERING_FILE:
			return "Transfering file to SD"
		if self._state == self.STATE_LOCKED:
			return "Locked"
		if self._state == self.STATE_HOMING:
			return "Homing"
		if self._state == self.STATE_FLASHING:
			return "Flashing"
		return "?%d?" % (self._state)

	def getErrorString(self):
		return self._errorValue

	def isClosedOrError(self):
		return self._state == self.STATE_ERROR or self._state == self.STATE_CLOSED_WITH_ERROR or self._state == self.STATE_CLOSED

	def isError(self):
		return self._state == self.STATE_ERROR or self._state == self.STATE_CLOSED_WITH_ERROR

	def isOperational(self):
		return self._state == self.STATE_OPERATIONAL or self._state == self.STATE_PRINTING or self._state == self.STATE_PAUSED or self._state == self.STATE_TRANSFERING_FILE

	def isLocked(self):
		return self._state == self.STATE_LOCKED

	def isHoming(self):
		return self._state == self.STATE_HOMING

	def isFlashing(self):
		return self._state == self.STATE_FLASHING

	def isPrinting(self):
		return self._state == self.STATE_PRINTING

	def isSdPrinting(self):
		return self.isSdFileSelected() and self.isPrinting()

	def isSdFileSelected(self):
		return self._currentFile is not None and isinstance(self._currentFile, PrintingSdFileInformation)

	def isStreaming(self):
		return self._currentFile is not None and isinstance(self._currentFile, StreamingGcodeFileInformation)

	def isPaused(self):
		return self._state == self.STATE_PAUSED

	def isBusy(self):
		return self.isPrinting() or self.isPaused()

	def isSdReady(self):
		return self._sdAvailable

	def getPrintProgress(self):
		if self._currentFile is None:
			return None
		return self._currentFile.getProgress()

	def getPrintFilepos(self):
		if self._currentFile is None:
			return None
		return self._currentFile.getFilepos()

	def getPrintTime(self):
		if self._currentFile is None or self._currentFile.getStartTime() is None:
			return None
		else:
			return time.time() - self._currentFile.getStartTime() - self._pauseWaitTimeLost

	def getCleanedPrintTime(self):
		printTime = self.getPrintTime()
		if printTime is None:
			return None

		cleanedPrintTime = printTime - self._heatupWaitTimeLost
		if cleanedPrintTime < 0:
			cleanedPrintTime = 0.0
		return cleanedPrintTime

	def getTemp(self):
		return self._temp

	def getBedTemp(self):
		return self._bedTemp

	def getOffsets(self):
		return dict(self._tempOffsets)

	def getCurrentTool(self):
		return self._currentTool

	def getConnection(self):
		return self._port, self._baudrate

	def getTransport(self):
		return self._serial

	##~~ external interface

	def close(self, isError = False):
		if self._temperature_timer is not None:
			try:
				self._temperature_timer.cancel()
				self._temperature_timer = None
			except AttributeError:
				pass

		if self._sd_status_timer is not None:
			try:
				self._sd_status_timer.cancel()
			except AttributeError:
				pass

		self._monitoring_active = False
		self._send_queue_active = False

		printing = self.isPrinting() or self.isPaused()
		if self._serial is not None:
			if isError:
				self._changeState(self.STATE_CLOSED_WITH_ERROR)
			else:
				self._changeState(self.STATE_CLOSED)
			self._serial.close()
		self._serial = None

		if settings().get(["feature", "sdSupport"]):
			self._sdFileList = []

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

	def setTemperatureOffset(self, offsets):
		self._tempOffsets.update(offsets)

	def fakeOk(self):
		self._clear_to_send.set()

	def sendCommand(self, cmd, cmd_type=None, processed=False):
		cmd = cmd.encode('ascii', 'replace')
		if not processed:
			cmd = process_gcode_line(cmd)
			if not cmd:
				return

		if cmd[0] == "/":
			specialcmd = cmd[1:].lower()
			if "togglestatusreport" in specialcmd:
				if self._temperature_timer is None:
					self._temperature_timer = RepeatedTimer(1, self._poll_temperature, run_first=True)
					self._temperature_timer.start()
				else:
					self._temperature_timer.cancel()
					self._temperature_timer = None
			elif "setstatusfrequency" in specialcmd:
				data = specialcmd.split(' ')
				try:
					frequency = float(data[1])
				except ValueError:
					self._log("No frequency setting found! Using 1 sec.")
					frequency = 1
				if self._temperature_timer is not None:
					self._temperature_timer.cancel()

				self._temperature_timer = RepeatedTimer(frequency, self._poll_temperature, run_first=True)
				self._temperature_timer.start()
			elif "disconnect" in specialcmd:
				self.close()
			else:
				self._log("Command not Found! %s" % cmd)
				self._log("available commands are:")
				self._log("   /togglestatusreport")
				self._log("   /setstatusfrequency <Inteval Sec>")
				self._log("   /disconnect")
			return

		eepromCmd = re.search("^\$[0-9]+=.+$", cmd)
		if(eepromCmd and self.isPrinting()):
			self._log("Warning: Configuration changes during print are not allowed!")

		if self.isPrinting() and not self.isSdFileSelected():
			self._commandQueue.put((cmd, cmd_type))
		elif self.isOperational() or self.isLocked() or self.isHoming():
			self._sendCommand(cmd, cmd_type=cmd_type)

	def sendGcodeScript(self, scriptName, replacements=None):
		context = dict()
		if replacements is not None and isinstance(replacements, dict):
			context.update(replacements)
		context.update(dict(
			printer_profile=self._printerProfileManager.get_current_or_default()
		))

		template = settings().loadScript("gcode", scriptName, context=context)
		if template is None:
			scriptLines = []
		else:
			scriptLines = filter(
				lambda x: x is not None and x.strip() != "",
				map(
					lambda x: process_gcode_line(x, offsets=self._tempOffsets, current_tool=self._currentTool),
					template.split("\n")
				)
			)

		for hook in self._gcodescript_hooks:
			try:
				retval = self._gcodescript_hooks[hook](self, "gcode", scriptName)
			except:
				self._logger.exception("Error while processing gcodescript hook %s" % hook)
			else:
				if retval is None:
					continue
				if not isinstance(retval, (list, tuple)) or not len(retval) == 2:
					continue

				def to_list(data):
					if isinstance(data, str):
						data = map(str.strip, data.split("\n"))
					elif isinstance(data, unicode):
						data = map(unicode.strip, data.split("\n"))

					if isinstance(data, (list, tuple)):
						return list(data)
					else:
						return None

				prefix, suffix = map(to_list, retval)
				if prefix:
					scriptLines = list(prefix) + scriptLines
				if suffix:
					scriptLines += list(suffix)

		for line in scriptLines:
			self.sendCommand(line)
		return "\n".join(scriptLines)

	def startPrint(self):
		if not self.isOperational() or self.isPrinting():
			return

		if self._currentFile is None:
			raise ValueError("No file selected for printing")

		self._heatupWaitStartTime = None
		self._heatupWaitTimeLost = 0.0
		self._pauseWaitStartTime = 0
		self._pauseWaitTimeLost = 0.0

		try:
			# TODO fetch init sequence from machine profile
			#self.sendCommand("$H") # homing here results in invalid GCode ID33 on G02/03 commands. WTF?
			#self.sendCommand("G92X0Y0Z0")
			#self.sendCommand("G90")
			#self.sendCommand("G21")

			# ensure fan is on whatever gcode follows.
			self.sendCommand("M08")

			self._currentFile.start()

			self._changeState(self.STATE_PRINTING)

			#self.sendCommand("M110 N0")

			payload = {
				"file": self._currentFile.getFilename(),
				"filename": os.path.basename(self._currentFile.getFilename()),
				"origin": self._currentFile.getFileLocation()
			}
			eventManager().fire(Events.PRINT_STARTED, payload)
			self.sendGcodeScript("beforePrintStarted", replacements=dict(event=payload))

			if self.isSdFileSelected():
				#self.sendCommand("M26 S0") # setting the sd post apparently sometimes doesn't work, so we re-select
				                            # the file instead

				# make sure to ignore the "file selected" later on, otherwise we'll reset our progress data
				self._ignore_select = True
				self.sendCommand("M23 {filename}".format(filename=self._currentFile.getFilename()))
				self._currentFile.setFilepos(0)

				self.sendCommand("M24")

				self._sd_status_timer = RepeatedTimer(lambda: get_interval("sdStatus"), self._poll_sd_status, run_first=True)
				self._sd_status_timer.start()
			else:
				line = self._getNext()
				if line is not None:
					self.sendCommand(line)

			# now make sure we actually do something, up until now we only filled up the queue
			self._sendFromQueue()
		except:
			self._logger.exception("Error while trying to start printing")
			self._errorValue = get_exception_string()
			self._changeState(self.STATE_ERROR)
			eventManager().fire(Events.ERROR, {"error": self.getErrorString()})

	def startFileTransfer(self, filename, localFilename, remoteFilename):
		if not self.isOperational() or self.isBusy():
			logging.info("Printer is not operation or busy")
			return

		self._currentFile = StreamingGcodeFileInformation(filename, localFilename, remoteFilename)
		self._currentFile.start()

		self.sendCommand("M28 %s" % remoteFilename)
		eventManager().fire(Events.TRANSFER_STARTED, {"local": localFilename, "remote": remoteFilename})
		self._callback.on_comm_file_transfer_started(remoteFilename, self._currentFile.getFilesize())

	def selectFile(self, filename, sd):
		if self.isBusy():
			return

		if sd:
			if not self.isOperational():
				# printer is not connected, can't use SD
				return
			self._sdFileToSelect = filename
			self.sendCommand("M23 %s" % filename)
		else:
			self._currentFile = PrintingGcodeFileInformation(filename, offsets_callback=self.getOffsets, current_tool_callback=self.getCurrentTool)
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

	def cancelPrint(self):
		if not self.isOperational() or self.isStreaming():
			return

		#self.sendCommand(chr(24)) # no necessity to reset

		self._changeState(self.STATE_OPERATIONAL)
		with self._send_queue.mutex:
			self._send_queue.queue.clear()
		self.sendCommand('M5')
		self.sendCommand('G0X0Y0')
		self.sendCommand('M9')

		if self.isSdFileSelected():
			self.sendCommand("M25")    # pause print
			self.sendCommand("M26 S0") # reset position in file to byte 0
			if self._sd_status_timer is not None:
				try:
					self._sd_status_timer.cancel()
				except:
					pass

		payload = {
			"file": self._currentFile.getFilename(),
			"filename": os.path.basename(self._currentFile.getFilename()),
			"origin": self._currentFile.getFileLocation()
		}

		#self.sendGcodeScript("afterPrintCancelled", replacements=dict(event=payload))
		eventManager().fire(Events.PRINT_CANCELLED, payload)

	def setPause(self, pause):
		if self.isStreaming():
			return

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

			#self._changeState(self.STATE_PRINTING)

			#self.sendGcodeScript("beforePrintResumed", replacements=dict(event=payload))

			self.sendCommand('~')
			if self.isSdFileSelected():
				self.sendCommand("M24")
				self.sendCommand("M27")
			else:
				line = self._getNext()
				if line is not None:
					self.sendCommand(line)

			# now make sure we actually do something, up until now we only filled up the queue
			self._sendFromQueue()

			eventManager().fire(Events.PRINT_RESUMED, payload)
		elif pause and self.isPrinting():
			if not self._pauseWaitStartTime:
				self._pauseWaitStartTime = time.time()

			self.sendCommand('!')
			#self._changeState(self.STATE_PAUSED)
			if self.isSdFileSelected():
				self.sendCommand("M25") # pause print
			#self.sendGcodeScript("afterPrintPaused", replacements=dict(event=payload))

			eventManager().fire(Events.PRINT_PAUSED, payload)

	def getSdFiles(self):
		return self._sdFiles

	def startSdFileTransfer(self, filename):
		if not self.isOperational() or self.isBusy():
			return

		self._changeState(self.STATE_TRANSFERING_FILE)
		self.sendCommand("M28 %s" % filename.lower())

	def endSdFileTransfer(self, filename):
		if not self.isOperational() or self.isBusy():
			return

		self.sendCommand("M29 %s" % filename.lower())
		self._changeState(self.STATE_OPERATIONAL)
		self.refreshSdFiles()

	def deleteSdFile(self, filename):
		if not self.isOperational() or (self.isBusy() and
				isinstance(self._currentFile, PrintingSdFileInformation) and
				self._currentFile.getFilename() == filename):
			# do not delete a file from sd we are currently printing from
			return

		self.sendCommand("M30 %s" % filename.lower())
		self.refreshSdFiles()

	def refreshSdFiles(self):
		if not self.isOperational() or self.isBusy():
			return
		self.sendCommand("M20")

	def initSdCard(self):
		if not self.isOperational():
			return
		self.sendCommand("M21")
		if settings().getBoolean(["feature", "sdAlwaysAvailable"]):
			self._sdAvailable = True
			self.refreshSdFiles()
			self._callback.on_comm_sd_state_change(self._sdAvailable)

	def releaseSdCard(self):
		if not self.isOperational() or (self.isBusy() and self.isSdFileSelected()):
			# do not release the sd card if we are currently printing from it
			return

		self.sendCommand("M22")
		self._sdAvailable = False
		self._sdFiles = []

		self._callback.on_comm_sd_state_change(self._sdAvailable)
		self._callback.on_comm_sd_files(self._sdFiles)

	##~~ communication monitoring and handling

	def _parseTemperatures(self, line):
		result = {}
		maxToolNum = 0
		for match in re.finditer(self._regex_temp, line):
			tool = match.group(1)
			toolNumber = int(match.group(2)) if match.group(2) and len(match.group(2)) > 0 else None
			if toolNumber > maxToolNum:
				maxToolNum = toolNumber

			try:
				actual = float(match.group(3))
				target = None
				if match.group(4) and match.group(5):
					target = float(match.group(5))

				result[tool] = (toolNumber, actual, target)
			except ValueError:
				# catch conversion issues, we'll rather just not get the temperature update instead of killing the connection
				pass

		if "T0" in result.keys() and "T" in result.keys():
			del result["T"]

		return maxToolNum, result

	def _processTemperatures(self, line):
		maxToolNum, parsedTemps = self._parseTemperatures(line)

		# extruder temperatures
		if not "T0" in parsedTemps.keys() and not "T1" in parsedTemps.keys() and "T" in parsedTemps.keys():
			# no T1 so only single reporting, "T" is our one and only extruder temperature
			toolNum, actual, target = parsedTemps["T"]

			if target is not None:
				self._temp[0] = (actual, target)
			elif 0 in self._temp.keys() and self._temp[0] is not None and isinstance(self._temp[0], tuple):
				(oldActual, oldTarget) = self._temp[0]
				self._temp[0] = (actual, oldTarget)
			else:
				self._temp[0] = (actual, None)
		elif not "T0" in parsedTemps.keys() and "T" in parsedTemps.keys():
			# Smoothieware sends multi extruder temperature data this way: "T:<first extruder> T1:<second extruder> ..." and therefore needs some special treatment...
			_, actual, target = parsedTemps["T"]
			del parsedTemps["T"]
			parsedTemps["T0"] = (0, actual, target)

		if "T0" in parsedTemps.keys():
			for n in range(maxToolNum + 1):
				tool = "T%d" % n
				if not tool in parsedTemps.keys():
					continue

				toolNum, actual, target = parsedTemps[tool]
				if target is not None:
					self._temp[toolNum] = (actual, target)
				elif toolNum in self._temp.keys() and self._temp[toolNum] is not None and isinstance(self._temp[toolNum], tuple):
					(oldActual, oldTarget) = self._temp[toolNum]
					self._temp[toolNum] = (actual, oldTarget)
				else:
					self._temp[toolNum] = (actual, None)

		# bed temperature
		if "B" in parsedTemps.keys():
			toolNum, actual, target = parsedTemps["B"]
			if target is not None:
				self._bedTemp = (actual, target)
			elif self._bedTemp is not None and isinstance(self._bedTemp, tuple):
				(oldActual, oldTarget) = self._bedTemp
				self._bedTemp = (actual, oldTarget)
			else:
				self._bedTemp = (actual, None)

	##~~ Serial monitor processing received messages

	@staticmethod
	def _writeGrblVersionToFile(versionDict):
		if versionDict['dirty'] == '-dirty':
			versionDict['dirty'] = True
		versionDict['lastConnect'] = time.time()
		import yaml
		versionFile = os.path.join(settings().getBaseFolder("logs"), 'grbl_Version.yml')
		with open(versionFile, 'w') as outfile:
			outfile.write(yaml.dump(versionDict, default_flow_style=True))

	def _compareGrblVersion(self, versionDict):
		cwd = os.path.dirname(__file__)
		with open(cwd + "/../grbl/grblVersionRequirement.yml", 'r') as infile:
			import yaml
			grblReqDict = yaml.load(infile)
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
		import subprocess

		# TODO check if avrdude is installed.
		# TODO log in logfile as well, not only to the serial monitor (use self._logger.info()... )
		params = ["avrdude", "-patmega328p", "-carduino", "-b" + str(self._baudrate), "-P" + str(self._port), "-D", "-Uflash:w:" + pathToGrblHex]
		returnCode = subprocess.call(params)

		if returnCode == False:
			self._log("successfully flashed new grbl version")
			self._openSerial()
			self._changeState(self.STATE_CONNECTING)
		else:
			self._log("error during flashing of new grbl version")
			self._errorValue = "avrdude returncode: %s" % returnCode
			self._changeState(self.STATE_CLOSED_WITH_ERROR)

	def _monitor(self):
		feedback_controls, feedback_matcher = convert_feedback_controls(settings().get(["controls"]))
		feedback_errors = []
		pause_triggers = convert_pause_triggers(settings().get(["printerParameters", "pauseTriggers"]))

		disable_external_heatup_detection = not settings().getBoolean(["feature", "externalHeatupDetection"])

		#Open the serial port.
		if not self._openSerial():
			return

		try_hello = not settings().getBoolean(["feature", "waitForStartOnConnect"])

		self._log("Connected to: %s, starting monitor" % self._serial)
		if self._baudrate == 0:
			self._serial.timeout = 0.01
			try_hello = False
			self._log("Starting baud rate detection")
			self._changeState(self.STATE_DETECT_BAUDRATE)
		else:
			self._changeState(self.STATE_CONNECTING)

		#Start monitoring the serial port.
		self._timeout = get_new_timeout("communication")

		startSeen = False
		supportRepetierTargetTemp = settings().getBoolean(["feature", "repetierTargetTemp"])
		supportWait = settings().getBoolean(["feature", "supportWait"])

		connection_timeout = settings().getFloat(["serial", "timeout", "connection"])
		detection_timeout = settings().getFloat(["serial", "timeout", "detection"])

		grblMoving = True
		grblLastStatus = ""

		# enqueue an M105 first thing
		if try_hello:
			self._sendCommand("?")
			self._clear_to_send.set()

		while self._monitoring_active:
			try:
				line = self._readline()
				if line is None:
					break
				if line.strip() is not "":
					self._timeout = get_new_timeout("communication")

				##~~ debugging output handling
				if line.startswith("//"):
					debugging_output = line[2:].strip()
					if debugging_output.startswith("action:"):
						action_command = debugging_output[len("action:"):].strip()

						if action_command == "pause":
							self._log("Pausing on request of the printer...")
							self.setPause(True)
						elif action_command == "resume":
							self._log("Resuming on request of the printer...")
							self.setPause(False)
						elif action_command == "disconnect":
							self._log("Disconnecting on request of the printer...")
							self._callback.on_comm_force_disconnect()
						else:
							for hook in self._printer_action_hooks:
								self._printer_action_hooks[hook](self, line, action_command)
					else:
						continue

				##~~ Error handling
				#line = self._handleErrors(line)

				# GRBL Position update
				if self._grbl :
					if(self._state == self.STATE_HOMING and 'ok' in line):
						self._changeState(self.STATE_OPERATIONAL)
						self._onHomingDone();

					# TODO check if "Alarm" is enough
					if("Alarm lock" in line):
						self._changeState(self.STATE_LOCKED)
					if("['$H'|'$X' to unlock]" in line):
						self._changeState(self.STATE_LOCKED)

					# TODO maybe better in _gcode_X_sent ...
					if("Idle" in line and (self._state == self.STATE_LOCKED)   ):
						self._changeState(self.STATE_OPERATIONAL)

					# TODO highly experimental. needs testing.
					if("Hold" in line and self._state == self.STATE_PRINTING):
						self._changeState(self.STATE_PAUSED)
					#if("Run" in line and self._state == self.STATE_PAUSED):
					#	self._changeState(self.STATE_PRINTING)

					if 'MPos:' in line:
						# check movement
						if grblLastStatus == line:
							grblMoving = False
						else:
							grblMoving = True
						grblLastStatus = line

						self._update_grbl_pos(line)

					if("ALARM: Hard/soft limit" in line):
						errorMsg = "Machine Limit Hit. Please reset the machine and do a homing cycle"
						self._log(errorMsg)
						self._errorValue = errorMsg
						eventManager().fire(Events.ERROR, {"error": self.getErrorString()})
						eventManager().fire(Events.LIMITS_HIT, {"error": self.getErrorString()})
						self._openSerial()
						self._changeState(self.STATE_CONNECTING)

					if("Invalid gcode" in line and self._state == self.STATE_PRINTING):
						# TODO Pause machine instead of resetting it.
						errorMsg = line
						self._log(errorMsg)
						self._errorValue = errorMsg
#						self._changeState(self.STATE_ERROR)
						eventManager().fire(Events.ERROR, {"error": self.getErrorString()})
						self._openSerial()
						self._changeState(self.STATE_CONNECTING)

					if("Grbl" in line and self._state == self.STATE_PRINTING):
						errorMsg = "Machine reset."
						self._log(errorMsg)
						self._errorValue = errorMsg
						self._changeState(self.STATE_LOCKED)
						eventManager().fire(Events.ERROR, {"error": self.getErrorString()})

					if("Grbl" in line):
						versionMatch = re.search("Grbl (?P<grbl>.+?)(_(?P<git>[0-9a-f]{7})(?P<dirty>-dirty)?)? \[.+\]", line)
						if(versionMatch):
							versionDict = versionMatch.groupdict()
							self._writeGrblVersionToFile(versionDict)
							if self._compareGrblVersion(versionDict) is False:
								self._flashGrbl()

					if("error:" in line):
						self.handle_grbl_error(line)

#				##~~ SD file list
#				# if we are currently receiving an sd file list, each line is just a filename, so just read it and abort processing
#				if self._sdFileList and not "End file list" in line:
#					preprocessed_line = line.strip().lower()
#					fileinfo = preprocessed_line.rsplit(None, 1)
#					if len(fileinfo) > 1:
#						# we might have extended file information here, so let's split filename and size and try to make them a bit nicer
#						filename, size = fileinfo
#						try:
#							size = int(size)
#						except ValueError:
#							# whatever that was, it was not an integer, so we'll just use the whole line as filename and set size to None
#							filename = preprocessed_line
#							size = None
#					else:
#						# no extended file information, so only the filename is there and we set size to None
#						filename = preprocessed_line
#						size = None
#
#					if valid_file_type(filename, "machinecode"):
#						if filter_non_ascii(filename):
#							self._logger.warn("Got a file from printer's SD that has a non-ascii filename (%s), that shouldn't happen according to the protocol" % filename)
#						else:
#							if not filename.startswith("/"):
#								# file from the root of the sd -- we'll prepend a /
#								filename = "/" + filename
#							self._sdFiles.append((filename, size))
#						continue

				##~~ process oks
				if line.strip().startswith("ok") or (self.isPrinting() and supportWait and line.strip().startswith("wait")):
					self._clear_to_send.set()
					self._long_running_command = False

#				##~~ Temperature processing
#				if ' T:' in line or line.startswith('T:') or ' T0:' in line or line.startswith('T0:') or ' B:' in line or line.startswith('B:'):
#					if not disable_external_heatup_detection and not line.strip().startswith("ok") and not self._heating:
#						self._logger.debug("Externally triggered heatup detected")
#						self._heating = True
#						self._heatupWaitStartTime = time.time()
#					self._processTemperatures(line)
#					self._callback.on_comm_temperature_update(self._temp, self._bedTemp)
#
#				elif supportRepetierTargetTemp and ('TargetExtr' in line or 'TargetBed' in line):
#					matchExtr = self._regex_repetierTempExtr.match(line)
#					matchBed = self._regex_repetierTempBed.match(line)
#
#					if matchExtr is not None:
#						toolNum = int(matchExtr.group(1))
#						try:
#							target = float(matchExtr.group(2))
#							if toolNum in self._temp.keys() and self._temp[toolNum] is not None and isinstance(self._temp[toolNum], tuple):
#								(actual, oldTarget) = self._temp[toolNum]
#								self._temp[toolNum] = (actual, target)
#							else:
#								self._temp[toolNum] = (None, target)
#							self._callback.on_comm_temperature_update(self._temp, self._bedTemp)
#						except ValueError:
#							pass
#					elif matchBed is not None:
#						try:
#							target = float(matchBed.group(1))
#							if self._bedTemp is not None and isinstance(self._bedTemp, tuple):
#								(actual, oldTarget) = self._bedTemp
#								self._bedTemp = (actual, target)
#							else:
#								self._bedTemp = (None, target)
#							self._callback.on_comm_temperature_update(self._temp, self._bedTemp)
#						except ValueError:
#							pass

#				#If we are waiting for an M109 or M190 then measure the time we lost during heatup, so we can remove that time from our printing time estimate.
#				if 'ok' in line and self._heatupWaitStartTime:
#					self._heatupWaitTimeLost = self._heatupWaitTimeLost + (time.time() - self._heatupWaitStartTime)
#					self._heatupWaitStartTime = None
#					self._heating = False

#				##~~ SD Card handling
#				elif 'SD init fail' in line or 'volume.init failed' in line or 'openRoot failed' in line:
#					self._sdAvailable = False
#					self._sdFiles = []
#					self._callback.on_comm_sd_state_change(self._sdAvailable)
#				elif 'Not SD printing' in line:
#					if self.isSdFileSelected() and self.isPrinting():
#						# something went wrong, printer is reporting that we actually are not printing right now...
#						self._sdFilePos = 0
#						self._changeState(self.STATE_OPERATIONAL)
#				elif 'SD card ok' in line and not self._sdAvailable:
#					self._sdAvailable = True
#					self.refreshSdFiles()
#					self._callback.on_comm_sd_state_change(self._sdAvailable)
#				elif 'Begin file list' in line:
#					self._sdFiles = []
#					self._sdFileList = True
#				elif 'End file list' in line:
#					self._sdFileList = False
#					self._callback.on_comm_sd_files(self._sdFiles)
#				elif 'SD printing byte' in line and self.isSdPrinting():
#					# answer to M27, at least on Marlin, Repetier and Sprinter: "SD printing byte %d/%d"
#					match = self._regex_sdPrintingByte.search(line)
#					self._currentFile.setFilepos(int(match.group(1)))
#					self._callback.on_comm_progress()
#				elif 'File opened' in line and not self._ignore_select:
#					# answer to M23, at least on Marlin, Repetier and Sprinter: "File opened:%s Size:%d"
#					match = self._regex_sdFileOpened.search(line)
#					if self._sdFileToSelect:
#						name = self._sdFileToSelect
#						self._sdFileToSelect = None
#					else:
#						name = match.group(1)
#					self._currentFile = PrintingSdFileInformation(name, int(match.group(2)))
#				elif 'File selected' in line:
#					if self._ignore_select:
#						self._ignore_select = False
#					elif self._currentFile is not None:
#						# final answer to M23, at least on Marlin, Repetier and Sprinter: "File selected"
#						self._callback.on_comm_file_selected(self._currentFile.getFilename(), self._currentFile.getFilesize(), True)
#						eventManager().fire(Events.FILE_SELECTED, {
#							"file": self._currentFile.getFilename(),
#							"origin": self._currentFile.getFileLocation()
#						})
#				elif 'Writing to file' in line:
#					# anwer to M28, at least on Marlin, Repetier and Sprinter: "Writing to file: %s"
#					self._changeState(self.STATE_PRINTING)
#					self._clear_to_send.set()
#					line = "ok"
#				elif 'Done printing file' in line and self.isSdPrinting():
#					# printer is reporting file finished printing
#					self._sdFilePos = 0
#					self._callback.on_comm_print_job_done()
#					self._changeState(self.STATE_OPERATIONAL)
#					eventManager().fire(Events.PRINT_DONE, {
#						"file": self._currentFile.getFilename(),
#						"filename": os.path.basename(self._currentFile.getFilename()),
#						"origin": self._currentFile.getFileLocation(),
#						"time": self.getPrintTime()
#					})
#					if self._sd_status_timer is not None:
#						try:
#							self._sd_status_timer.cancel()
#						except:
#							pass
#				elif 'Done saving file' in line:
#					self.refreshSdFiles()
#				elif 'File deleted' in line and line.strip().endswith("ok"):
#					# buggy Marlin version that doesn't send a proper \r after the "File deleted" statement, fixed in
#					# current versions
#					self._clear_to_send.set()

				##~~ Message handling
				elif line.strip() != '' \
						and line.strip() != 'ok' and not line.startswith("wait") \
						and not line.startswith('Resend:') \
						and line != 'echo:Unknown command:""\n' \
						and line != "Unsupported statement" \
						and self.isOperational():
					self._callback.on_comm_message(line)

				##~~ Parsing for feedback commands
				if feedback_controls and feedback_matcher and not "_all" in feedback_errors:
					try:
						self._process_registered_message(line, feedback_matcher, feedback_controls, feedback_errors)
					except:
						# something went wrong while feedback matching
						self._logger.exception("Error while trying to apply feedback control matching, disabling it")
						feedback_errors.append("_all")

				##~~ Parsing for pause triggers

				if pause_triggers and not self.isStreaming():
					if "enable" in pause_triggers.keys() and pause_triggers["enable"].search(line) is not None:
						self.setPause(True)
					elif "disable" in pause_triggers.keys() and pause_triggers["disable"].search(line) is not None:
						self.setPause(False)
					elif "toggle" in pause_triggers.keys() and pause_triggers["toggle"].search(line) is not None:
						self.setPause(not self.isPaused())

				### Baudrate detection
				if self._state == self.STATE_DETECT_BAUDRATE:
					if line == '' or time.time() > self._timeout:
						if self._baudrateDetectRetry > 0:
							self._serial.timeout = detection_timeout
							self._baudrateDetectRetry -= 1
							self._serial.write('\n')
							self._log("Baudrate test retry: %d" % (self._baudrateDetectRetry))
							if self._grbl:
								self._sendCommand("$")
							else:
								self._sendCommand("M110")
							self._clear_to_send.set()
						elif len(self._baudrateDetectList) > 0:
							baudrate = self._baudrateDetectList.pop(0)
							try:
								self._serial.baudrate = baudrate
								if self._serial.timeout != connection_timeout:
									self._serial.timeout = connection_timeout
								self._log("Trying baudrate: %d" % (baudrate))
								self._baudrateDetectRetry = 5
								self._timeout = get_new_timeout("communication")
								self._serial.write('\n')
								if self._grbl:
									self._sendCommand("$")
								else:
									self._sendCommand("M110")
								self._clear_to_send.set()
							except:
								self._log("Unexpected error while setting baudrate: %d %s" % (baudrate, get_exception_string()))
						else:
							self.close()
							self._errorValue = "No more baudrates to test, and no suitable baudrate found."
							self._changeState(self.STATE_ERROR)
							eventManager().fire(Events.ERROR, {"error": self.getErrorString()})
					elif 'start' in line or 'ok' in line:
						self._onConnected(self.STATE_LOCKED)
						self._clear_to_send.set()



				### Connection attempt
				elif self._state == self.STATE_CONNECTING:
					#line = self.__readline()
					if "Grbl" in line:
						self._onConnected(self.STATE_LOCKED)
#						self._clear_to_send.set()
					elif "<Idle" in line:
						self._onConnected(self.STATE_OPERATIONAL)
					# elif time.time() > self._timeout:
					# 	print("TIMEOUT_CLOSE")
					# 	self.close()

				### Operational
				elif self._state == self.STATE_OPERATIONAL or self._state == self.STATE_PAUSED:
					if "ok" in line:
						# if we still have commands to process, process them
						if self._resendSwallowNextOk:
							self._resendSwallowNextOk = False
						elif self._resendDelta is not None:
							self._resendNextCommand()
						elif self._sendFromQueue():
							pass

					# resend -> start resend procedure from requested line
					elif line.lower().startswith("resend") or line.lower().startswith("rs"):
						self._handleResendRequest(line)

				### Printing
				elif self._state == self.STATE_PRINTING:
					if line == "" and time.time() > self._timeout:
						if not self._long_running_command:
							self._log("Communication timeout during printing, forcing a line")
							self._sendCommand("?")
							self._clear_to_send.set()
						else:
							self._logger.debug("Ran into a communication timeout, but a command known to be a long runner is currently active")

					if "ok" in line or (supportWait and "wait" in line):
						# a wait while printing means our printer's buffer ran out, probably due to some ok getting
						# swallowed, so we treat it the same as an ok here teo take up communication again
						if self._resendSwallowNextOk:
							self._resendSwallowNextOk = False

						elif self._resendDelta is not None:
							self._resendNextCommand()

						else:
							if self._sendFromQueue():
								pass
							elif not self.isSdPrinting():
								self._sendNext()

					elif line.lower().startswith("resend") or line.lower().startswith("rs"):
						self._handleResendRequest(line)
			except:
				self._logger.exception("Something crashed inside the serial connection loop, please report this in OctoPrint's bug tracker:")

				errorMsg = "See octoprint.log for details"
				self._log(errorMsg)
				self._errorValue = errorMsg
				self._changeState(self.STATE_ERROR)
				eventManager().fire(Events.ERROR, {"error": self.getErrorString()})
		self._log("Connection closed, closing down monitor")

	def _process_registered_message(self, line, feedback_matcher, feedback_controls, feedback_errors):
		feedback_match = feedback_matcher.search(line)
		if feedback_match is None:
			return

		for match_key in feedback_match.groupdict():
			try:
				feedback_key = match_key[len("group"):]
				if not feedback_key in feedback_controls or feedback_key in feedback_errors or feedback_match.group(match_key) is None:
					continue
				matched_part = feedback_match.group(match_key)

				if feedback_controls[feedback_key]["matcher"] is None:
					continue

				match = feedback_controls[feedback_key]["matcher"].search(matched_part)
				if match is None:
					continue

				outputs = dict()
				for template_key, template in feedback_controls[feedback_key]["templates"].items():
					try:
						output = template.format(*match.groups())
					except KeyError:
						output = template.format(**match.groupdict())
					except:
						output = None

					if output is not None:
						outputs[template_key] = output
				eventManager().fire(Events.REGISTERED_MESSAGE_RECEIVED, dict(key=feedback_key, matched=matched_part, outputs=outputs))
			except:
				self._logger.exception("Error while trying to match feedback control output, disabling key {key}".format(key=match_key))
				feedback_errors.append(match_key)

	def _poll_temperature(self):
		"""
		Polls the temperature after the temperature timeout, re-enqueues itself.

		If the printer is not operational, not printing from sd, busy with a long running command or heating, no poll
		will be done.
		"""

		if self.isOperational() and not self.isStreaming() and not self._long_running_command and not self._heating:
			self.sendCommand("?", cmd_type="temperature_poll")

	def _poll_sd_status(self):
		"""
		Polls the sd printing status after the sd status timeout, re-enqueues itself.

		If the printer is not operational, not printing from sd, busy with a long running command or heating, no poll
		will be done.
		"""

		if self.isOperational() and self.isSdPrinting() and not self._long_running_command and not self._heating:
			self.sendCommand("M27", cmd_type="sd_status_poll")

	def _onConnected(self, nextState):
		self._serial.timeout = settings().getFloat(["serial", "timeout", "communication"])
		#self._temperature_timer = RepeatedTimer(lambda: get_interval("temperature"), self._poll_temperature, run_first=True)
		if self._temperature_timer is not None:
			self._temperature_timer.cancel()
		self._temperature_timer = RepeatedTimer(1, self._poll_temperature, run_first=True)
		self._temperature_timer.start()

		if(nextState == None):
			self._changeState(self.STATE_LOCKED)
		else:
			self._changeState(nextState)

		#if self._sdAvailable:
		#	self.refreshSdFiles()
		#else:
		#	self.initSdCard()

		payload = dict(port=self._port, baudrate=self._baudrate)
		eventManager().fire(Events.CONNECTED, payload)
		#self.sendGcodeScript("afterPrinterConnected", replacements=dict(event=payload))

	def _onHomingDone(self):
		self.sendCommand("G92X0Y0Z0")
		self.sendCommand("G90")
		self.sendCommand("G21")


	def _sendFromQueue(self):
		if not self._commandQueue.empty() and not self.isStreaming():
			entry = self._commandQueue.get()
			if isinstance(entry, tuple):
				if not len(entry) == 2:
					return False
				cmd, cmd_type = entry
			else:
				cmd = entry
				cmd_type = None

			self._sendCommand(cmd, cmd_type=cmd_type)
			return True
		else:
			return False

	def _detectPort(self, close):
		programmer = stk500v2.Stk500v2()
		self._log("Serial port list: %s" % (str(serialList())))
		for p in serialList():
			serial_obj = None

			try:
				self._log("Connecting to: %s" % (p))
				programmer.connect(p)
				serial_obj = programmer.leaveISP()
			except ispBase.IspError as (e):
				self._log("Error while connecting to %s: %s" % (p, str(e)))
			except:
				self._log("Unexpected error while connecting to serial port: %s %s" % (p, get_exception_string()))

			if serial_obj is not None:
				if (close):
					serial_obj.close()
				return serial_obj

			programmer.close()
		return None

	def _openSerial(self):
		def default(_, port, baudrate, read_timeout):
			if port is None or port == 'AUTO':
				# no known port, try auto detection
				self._changeState(self.STATE_DETECT_SERIAL)
				serial_obj = self._detectPort(True)
				if serial_obj is None:
					self._errorValue = 'Failed to autodetect serial port, please set it manually.'
					self._changeState(self.STATE_ERROR)
					eventManager().fire(Events.ERROR, {"error": self.getErrorString()})
					self._log("Failed to autodetect serial port, please set it manually.")
					return None

				port = serial_obj.port

			# connect to regular serial port
			self._log("Connecting to: %s" % port)
			if baudrate == 0:
				baudrates = baudrateList()
				serial_obj = serial.Serial(str(port), 115200 if 115200 in baudrates else baudrates[0], timeout=read_timeout, writeTimeout=10000, parity=serial.PARITY_ODD)
			else:
				serial_obj = serial.Serial(str(port), baudrate, timeout=read_timeout, writeTimeout=10000, parity=serial.PARITY_ODD)
			serial_obj.close()
			serial_obj.parity = serial.PARITY_NONE
			serial_obj.open()

			return serial_obj

		serial_factories = self._serial_factory_hooks.items() + [("default", default)]
		for name, factory in serial_factories:
			try:
				serial_obj = factory(self, self._port, self._baudrate, settings().getFloat(["serial", "timeout", "connection"]))
			except:
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
				self._serial = serial_obj
				self._clear_to_send.clear()
				return True

		return False

	def _handleErrors(self, line):
		# No matter the state, if we see an error, goto the error state and store the error for reference.
		if line.startswith('Error:') or line.startswith('!!'):
			#Oh YEAH, consistency.
			# Marlin reports an MIN/MAX temp error as "Error:x\n: Extruder switched off. MAXTEMP triggered !\n"
			#	But a bed temp error is reported as "Error: Temperature heated bed switched off. MAXTEMP triggered !!"
			#	So we can have an extra newline in the most common case. Awesome work people.
			if self._regex_minMaxError.match(line):
				line = line.rstrip() + self._readline()

			if 'line number' in line.lower() or 'checksum' in line.lower() or 'expected line' in line.lower():
				#Skip the communication errors, as those get corrected.
				self._lastCommError = line[6:] if line.startswith("Error:") else line[2:]
				pass
			elif 'volume.init' in line.lower() or "openroot" in line.lower() or 'workdir' in line.lower()\
					or "error writing to file" in line.lower():
				#Also skip errors with the SD card
				pass
			elif not self.isError():
				self._errorValue = line[6:] if line.startswith("Error:") else line[2:]
				self._changeState(self.STATE_ERROR)
				eventManager().fire(Events.ERROR, {"error": self.getErrorString()})
		return line

	def _readline(self):
		if self._serial == None:
			return None
		try:
			ret = self._serial.readline()
			if('ok' in ret or 'error' in ret):
				if(len(self.acc_line_lengths) > 0):
					#print('buffer',sum(self.acc_line_lengths), 'deleting after ok', self.acc_line_lengths[0])
					del self.acc_line_lengths[0]  # Delete the commands character count corresponding to the last 'ok'
		except:
			self._log("Unexpected error while reading serial port: %s" % (get_exception_string()))
			self._errorValue = get_exception_string()
			self.close(True)
			return None
		if ret == '':
			#self._log("Recv: TIMEOUT")
			return ''

		try:
			self._log("Recv: %s" % sanitize_ascii(ret))
		except ValueError as e:
			self._log("WARN: While reading last line: %s" % e)
			self._log("Recv: %r" % ret)

		return ret

	def _getNext(self):
		line = self._currentFile.getNext()
		if line is None:
			if self.isStreaming():
				self._sendCommand("M29")

				remote = self._currentFile.getRemoteFilename()
				payload = {
					"local": self._currentFile.getLocalFilename(),
					"remote": remote,
					"time": self.getPrintTime()
				}

				self._currentFile = None
				self._changeState(self.STATE_OPERATIONAL)
				self._callback.on_comm_file_transfer_done(remote)
				eventManager().fire(Events.TRANSFER_DONE, payload)
				self.refreshSdFiles()
			else:
				payload = {
					"file": self._currentFile.getFilename(),
					"filename": os.path.basename(self._currentFile.getFilename()),
					"origin": self._currentFile.getFileLocation(),
					"time": self.getPrintTime()
				}
				self._callback.on_comm_print_job_done()
				self._changeState(self.STATE_OPERATIONAL)
				eventManager().fire(Events.PRINT_DONE, payload)

				self._sendCommand("M5")
				self._sendCommand("G0X0Y0")
				self._sendCommand("M9")
				#self.sendGcodeScript("afterPrintDone", replacements=dict(event=payload))
		return line

	def _sendNext(self):
		with self._sendNextLock:
			line = self._getNext()
			if line is not None:
				self._sendCommand(line)
				self._callback.on_comm_progress()

	def _handleResendRequest(self, line):
		lineToResend = None
		try:
			lineToResend = int(line.replace("N:", " ").replace("N", " ").replace(":", " ").split()[-1])
		except:
			if "rs" in line:
				lineToResend = int(line.split()[1])

		if lineToResend is not None:
			self._resendSwallowNextOk = True

			lastCommError = self._lastCommError
			self._lastCommError = None

			resendDelta = self._currentLine - lineToResend

			if lastCommError is not None \
					and ("line number" in lastCommError.lower() or "expected line" in lastCommError.lower()) \
					and lineToResend == self._lastResendNumber \
					and self._resendDelta is not None and self._currentResendCount < self._resendDelta:
				self._logger.debug("Ignoring resend request for line %d, that still originates from lines we sent before we got the first resend request" % lineToResend)
				self._currentResendCount += 1
				return

			self._resendDelta = resendDelta
			self._lastResendNumber = lineToResend
			self._currentResendCount = 0

			if self._resendDelta > len(self._lastLines) or len(self._lastLines) == 0 or self._resendDelta < 0:
				self._errorValue = "Printer requested line %d but no sufficient history is available, can't resend" % lineToResend
				self._logger.warn(self._errorValue)
				if self.isPrinting():
					# abort the print, there's nothing we can do to rescue it now
					self._changeState(self.STATE_ERROR)
					eventManager().fire(Events.ERROR, {"error": self.getErrorString()})
				else:
					# reset resend delta, we can't do anything about it
					self._resendDelta = None
			else:
				self._resendNextCommand()

	def _resendNextCommand(self):
		self._lastCommError = None

		# Make sure we are only handling one sending job at a time
		with self._sendingLock:
			cmd = self._lastLines[-self._resendDelta]
			lineNumber = self._currentLine - self._resendDelta

			self._enqueue_for_sending(cmd, linenumber=lineNumber)

			self._resendDelta -= 1
			if self._resendDelta <= 0:
				self._resendDelta = None
				self._lastResendNumber = None
				self._currentResendCount = 0

	def _sendCommand(self, cmd, cmd_type=None):
		# Make sure we are only handling one sending job at a time
		with self._sendingLock:
			if self._serial is None:
				return

			gcode = None
			if not self.isStreaming():
				# trigger the "queuing" phase only if we are not streaming to sd right now
				cmd, cmd_type, gcode = self._process_command_phase("queuing", cmd, cmd_type, gcode=gcode)

				if cmd is None:
					# command is no more, return
					return

				if gcode and gcode in gcodeToEvent:
					# if this is a gcode bound to an event, trigger that now
					eventManager().fire(gcodeToEvent[gcode])

			# actually enqueue the command for sending
			self._enqueue_for_sending(cmd, command_type=cmd_type)

			if not self.isStreaming():
				# trigger the "queued" phase only if we are not streaming to sd right now
				self._process_command_phase("queued", cmd, cmd_type, gcode=gcode)

	def gcode_command_for_cmd(self, cmd):
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

	##~~ send loop handling

	def _enqueue_for_sending(self, command, linenumber=None, command_type=None):
		"""
		Enqueues a command an optional linenumber to use for it in the send queue.

		Arguments:
		    command (str): The command to send.
		    linenumber (int): The line number with which to send the command. May be ``None`` in which case the command
		        will be sent without a line number and checksum.
		"""

		try:
			self._send_queue.put((command, linenumber, command_type))
		except TypeAlreadyInQueue as e:
			self._logger.debug("Type already in queue: " + e.type)

	def _send_loop(self):
		"""
		The send loop is reponsible of sending commands in ``self._send_queue`` over the line, if it is cleared for
		sending (through received ``ok`` responses from the printer's firmware.
		"""

		self._clear_to_send.wait()

		while self._send_queue_active:
			try:
				peeked_entry = self._send_queue.peek()
				p_command, p_linenbr, p_cmd_type = peeked_entry

				if(self.RX_BUFFER_SIZE - sum(self.acc_line_lengths) - len(p_command) < 5):
					time.sleep(0.001)
					continue

				# wait until we have something in the queue
				entry = self._send_queue.get()

				# make sure we are still active
				if not self._send_queue_active:
					break

				# fetch command and optional linenumber from queue
				command, linenumber, command_type = entry

				# some firmwares (e.g. Smoothie) might support additional in-band communication that will not
				# stick to the acknowledgement behaviour of GCODE, so we check here if we have a GCODE command
				# at hand here and only clear our clear_to_send flag later if that's the case
				gcode = self.gcode_command_for_cmd(command)

				if linenumber is not None:
					# line number predetermined - this only happens for resends, so we'll use the number and
					# send directly without any processing (since that already took place on the first sending!)
					self._doSendWithChecksum(command, linenumber)

				else:
					# trigger "sending" phase
					command, _, gcode = self._process_command_phase("sending", command, command_type, gcode=gcode)

					if command is None:
						# so no, we are not going to send this, that was a last-minute bail, let's fetch the next item from the queue
						continue

					# now comes the part where we increase line numbers and send stuff - no turning back now
					if (not self._grbl and gcode is not None or self._sendChecksumWithUnknownCommands) and (self.isPrinting() or self._alwaysSendChecksum):
						linenumber = self._currentLine
						self._addToLastLines(command)
						self._currentLine += 1
						self._doSendWithChecksum(command, linenumber)
					else:
						self._doSendWithoutChecksum(command)

				# trigger "sent" phase and use up one "ok"
				self._process_command_phase("sent", command, command_type, gcode=gcode)

				# we only need to use up a clear if the command we just sent was either a gcode command or if we also
				# require ack's for unknown commands
				use_up_clear = self._unknownCommandsNeedAck
				if gcode is not None:
					use_up_clear = True

				# if we need to use up a clear, do that now
				if use_up_clear:
					self._clear_to_send.clear()

				# now we just wait for the next clear and then start again
				self._clear_to_send.wait()
			except:
				self._logger.exception("Caught an exception in the send loop")
		self._log("Closing down send loop")

	def _process_command_phase(self, phase, command, command_type=None, gcode=None):
		if phase not in ("queuing", "queued", "sending", "sent"):
			return command, command_type, gcode

		if gcode is None:
			gcode = self.gcode_command_for_cmd(command)

		# send it through the phase specific handlers provided by plugins
		for name, hook in self._gcode_hooks[phase].items():
			try:
				hook_result = hook(self, phase, command, command_type, gcode)
			except:
				self._logger.exception("Error while processing hook {name} for phase {phase} and command {command}:".format(**locals()))
			else:
				command, command_type, gcode = self._handle_command_handler_result(command, command_type, gcode, hook_result)
				if command is None:
					# hook handler return None as command, so we'll stop here and return a full out None result
					return None, None, None

		# if it's a gcode command send it through the specific handler if it exists
		if gcode is not None:
			gcodeHandler = "_gcode_" + gcode + "_" + phase
			if hasattr(self, gcodeHandler):
				handler_result = getattr(self, gcodeHandler)(command, cmd_type=command_type)
				command, command_type, gcode = self._handle_command_handler_result(command, command_type, gcode, handler_result)

		# send it through the phase specific command handler if it exists
		commandPhaseHandler = "_command_phase_" + phase
		if hasattr(self, commandPhaseHandler):
			handler_result = getattr(self, commandPhaseHandler)(command, cmd_type=command_type, gcode=gcode)
			command, command_type, gcode = self._handle_command_handler_result(command, command_type, gcode, handler_result)

		# finally return whatever we resulted on
		return command, command_type, gcode

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

		gcode = self.gcode_command_for_cmd(command)
		return command, command_type, gcode

	##~~ actual sending via serial

	def _doSendWithChecksum(self, cmd, lineNumber):
		commandToSend = "N%d %s" % (lineNumber, cmd)
		checksum = reduce(lambda x,y:x^y, map(ord, commandToSend))
		commandToSend = "%s*%d" % (commandToSend, checksum)
		self._doSendWithoutChecksum(commandToSend)

	def _doSendWithoutChecksum(self, cmd):
		if cmd == "?":
			try:
				self._serial.write(cmd)
			except serial.SerialTimeoutException:
				self._log("Serial timeout while writing to serial port, trying again.")
				try:
					self._serial.write(cmd)
				except:
					self._log("Unexpected error while writing serial port: %s" % (get_exception_string()))
					self._errorValue = get_exception_string()
					self.close(True)
		else:
			self._log("Send: %s" % cmd)
			self.acc_line_lengths.append(len(cmd)+1) # Track number of characters in grbl serial read buffer
			try:
				self._serial.write(cmd + '\n')
			except serial.SerialTimeoutException:
				self._log("Serial timeout while writing to serial port, trying again.")
				try:
					self._serial.write(cmd + '\n')
				except:
					self._log("Unexpected error while writing serial port: %s" % (get_exception_string()))
					self._errorValue = get_exception_string()
					self.close(True)
			except:
				self._log("Unexpected error while writing serial port: %s" % (get_exception_string()))
				self._errorValue = get_exception_string()
				self.close(True)

	##~~ command handlers
	def _gcode_H_sent(self, cmd, cmd_type=None):
		self._changeState(self.STATE_HOMING)
		return cmd

	def _gcode_Hold_sent(self, cmd, cmd_type=None):
		self._changeState(self.STATE_PAUSED)
		return cmd
	def _gcode_Resume_sent(self, cmd, cmd_type=None):
		self._changeState(self.STATE_PRINTING)
		return cmd

	def _gcode_T_sent(self, cmd, cmd_type=None):
		toolMatch = self._regex_paramTInt.search(cmd)
		if toolMatch:
			self._currentTool = int(toolMatch.group(1))

	def _gcode_G0_sent(self, cmd, cmd_type=None):
		if 'Z' in cmd:
			match = self._regex_paramZFloat.search(cmd)
			if match:
				try:
					z = float(match.group(1))
					if self._currentZ != z:
						self._currentZ = z
						self._callback.on_comm_z_change(z)
				except ValueError:
					pass
	_gcode_G1_sent = _gcode_G0_sent

	def _gcode_M0_queuing(self, cmd, cmd_type=None):
		self.setPause(True)
		return None, # Don't send the M0 or M1 to the machine, as M0 and M1 are handled as an LCD menu pause.
	_gcode_M1_queuing = _gcode_M0_queuing

	def _gcode_M104_sent(self, cmd, cmd_type=None):
		toolNum = self._currentTool
		toolMatch = self._regex_paramTInt.search(cmd)
		if toolMatch:
			toolNum = int(toolMatch.group(1))
		match = self._regex_paramSInt.search(cmd)
		if match:
			try:
				target = float(match.group(1))
				if toolNum in self._temp.keys() and self._temp[toolNum] is not None and isinstance(self._temp[toolNum], tuple):
					(actual, oldTarget) = self._temp[toolNum]
					self._temp[toolNum] = (actual, target)
				else:
					self._temp[toolNum] = (None, target)
			except ValueError:
				pass

	def _gcode_M140_sent(self, cmd, cmd_type=None):
		match = self._regex_paramSInt.search(cmd)
		if match:
			try:
				target = float(match.group(1))
				if self._bedTemp is not None and isinstance(self._bedTemp, tuple):
					(actual, oldTarget) = self._bedTemp
					self._bedTemp = (actual, target)
				else:
					self._bedTemp = (None, target)
			except ValueError:
				pass

	def _gcode_M109_sent(self, cmd, cmd_type=None):
		self._heatupWaitStartTime = time.time()
		self._long_running_command = True
		self._heating = True
		self._gcode_M104_sent(cmd, cmd_type)

	def _gcode_M190_sent(self, cmd, cmd_type=None):
		self._heatupWaitStartTime = time.time()
		self._long_running_command = True
		self._heating = True
		self._gcode_M140_sent(cmd, cmd_type)

	def _gcode_M110_sending(self, cmd, cmd_type=None):
		newLineNumber = None
		match = self._regex_paramNInt.search(cmd)
		if match:
			try:
				newLineNumber = int(match.group(1))
			except:
				pass
		else:
			newLineNumber = 0

		# send M110 command with new line number
		self._currentLine = newLineNumber

		# after a reset of the line number we have no way to determine what line exactly the printer now wants
		self._lastLines.clear()
		self._resendDelta = None

	def _gcode_M112_queuing(self, cmd, cmd_type=None): # It's an emergency what todo? Canceling the print should be the minimum
		self.cancelPrint()

	def _gcode_G4_sent(self, cmd, cmd_type=None):
		# we are intending to dwell for a period of time, increase the timeout to match
		cmd = cmd.upper()
		p_idx = cmd.find('P')
		s_idx = cmd.find('S')
		_timeout = 0
		if p_idx != -1:
			# dwell time is specified in milliseconds
			_timeout = float(cmd[p_idx+1:]) / 1000.0
		elif s_idx != -1:
			# dwell time is specified in seconds
			_timeout = float(cmd[s_idx+1:])
		self._timeout = get_new_timeout("communication") + _timeout

	##~~ command phase handlers

	def _command_phase_sending(self, cmd, cmd_type=None, gcode=None):
		if gcode is not None and gcode in self._long_running_commands:
			self._long_running_command = True

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

	def handle_grbl_error(self, line):
		#self._log("grbl error: %s" % line)
		if("Expected command letter" in line):
			#G-code is composed of G-code "words", which consists of a letter followed by a number value. This error occurs when the letter prefix of a G-code word is missing in the G-code block (aka line).
			pass

		elif("Bad number format" in line):
			#The number value suffix of a G-code word is missing in the G-code block, or when configuring a $Nx=line or $x=val Grbl setting and the x is not a number value.
			pass

		elif("Invalid statement" in line):
			#The issued Grbl $ system command is not recognized or is invalid.
			pass

		elif("Value < 0" in line):
			#The value of a $x=val Grbl setting, F feed rate, N line number, P word, T tool number, or S spindle speed is negative.
			pass

		elif("Setting disabled" in line):
			#Homing is disabled when issuing a $H command.
			pass

		elif("Value < 3 usec" in line):
			#Step pulse time length cannot be less than 3 microseconds (for technical reasons).
			pass

		elif("EEPROM read fail. Using defaults" in line):
			#If Grbl can't read data contained in the EEPROM, this error is returned. Grbl will also clear and restore the effected data back to defaults.
			pass

		elif("Not idle" in line):
			#Certain Grbl $ commands are blocked depending Grbl's current state, or what its doing. In general, Grbl blocks any command that fetches from or writes to the EEPROM since the AVR microcontroller will shutdown all of the interrupts for a few clock cycles when this happens. There is no work around, other than blocking it. This ensures both the serial and step generator interrupts are working smoothly throughout operation.
			pass

		elif("Alarm lock" in line):
			#Grbl enters an ALARM state when Grbl doesn't know where it is and will then block all G-code commands from being executed. This error occurs if G-code commands are sent while in the alarm state. Grbl has two alarm scenarios: When homing is enabled, Grbl automatically goes into an alarm state to remind the user to home before doing anything; When something has went critically wrong, usually when Grbl can't guarantee positioning. This typically happens when something causes Grbl to force an immediate stop while its moving from a hard limit being triggered or a user commands an ill-timed reset.
			pass

		elif("Homing not enabled" in line):
			#Soft limits cannot be enabled if homing is not enabled, because Grbl has no idea where it is when you startup your machine unless you perform a homing cycle.
			pass

		elif("Line overflow" in line):
			#Grbl has to do everything it does within 2KB of RAM. Not much at all. So, we had to make some decisions on what's important. Grbl limits the number of characters in each line to less than 80 characters (70 in v0.8, 50 in v0.7 or earlier), excluding spaces or comments. The G-code standard mandates 256 characters, but Grbl simply doesn't have the RAM to spare. However, we don't think there will be any problems with this with all of the expected G-code commands sent to Grbl. This error almost always occurs when a user or CAM-generated G-code program sends position values that are in double precision (i.e. -2.003928578394852), which is not realistic or physically possible. Users and GUIs need to send Grbl floating point values in single precision (i.e. -2.003929) to avoid this error.
			pass

		elif("Modal group violation" in line):
			#The G-code parser has detected two G-code commands that belong to the same modal group in the block/line. Modal groups are sets of G-code commands that mutually exclusive. For example, you can't issue both a G0 rapids and G2 arc in the same line, since they both need to use the XYZ target position values in the line. LinuxCNC.org has some great documentation on modal groups.
			pass

		elif("Unsupported command" in line):
			#The G-code parser doesn't recognize or support one of the G-code commands in the line. Check your G-code program for any unsupported commands and either remove them or update them to be compatible with Grbl.
			pass

		elif("Undefined feed rate" in line):
			#There is no feed rate programmed, and a G-code command that requires one is in the block/line. The G-code standard mandates F feed rates to be undefined upon a reset or when switching from inverse time mode to units mode. Older Grbl versions had a default feed rate setting, which was illegal and was removed in Grbl v0.9.
			pass

		elif("Invalid gcode ID" in line):
			#XX
			pass


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

### Printing file information classes ##################################################################################

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

class PrintingSdFileInformation(PrintingFileInformation):
	"""
	Encapsulates information regarding an ongoing print from SD.
	"""

	def __init__(self, filename, size):
		PrintingFileInformation.__init__(self, filename)
		self._size = size

	def setFilepos(self, pos):
		"""
		Sets the current file position.
		"""
		self._pos = pos

	def getFileLocation(self):
		return FileDestinations.SDCARD

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
			offsets = self._offsets_callback() if self._offsets_callback is not None else None
			current_tool = self._current_tool_callback() if self._current_tool_callback is not None else None

			processed = None
			while processed is None:
				if self._handle is None:
					# file got closed just now
					return None
				line = self._handle.readline()
				if not line:
					self.close()
				processed = process_gcode_line(line, offsets=offsets, current_tool=current_tool)
			self._pos = self._handle.tell()

			return processed
		except Exception as e:
			self.close()
			self._logger.exception("Exception while processing line")
			raise e

class StreamingGcodeFileInformation(PrintingGcodeFileInformation):
	def __init__(self, path, localFilename, remoteFilename):
		PrintingGcodeFileInformation.__init__(self, path)
		self._localFilename = localFilename
		self._remoteFilename = remoteFilename

	def start(self):
		PrintingGcodeFileInformation.start(self)
		self._start_time = time.time()

	def getLocalFilename(self):
		return self._localFilename

	def getRemoteFilename(self):
		return self._remoteFilename


class TypedQueue(queue.Queue):

	def __init__(self, maxsize=0):
		queue.Queue.__init__(self, maxsize=maxsize)
		self._lookup = []
		self._peekedItem = None;

	def _put(self, item):
		if isinstance(item, tuple) and len(item) == 3:
			cmd, line, cmd_type = item
			if cmd_type is not None:
				if cmd_type in self._lookup:
					raise TypeAlreadyInQueue(cmd_type, "Type {cmd_type} is already in queue".format(**locals()))
				else:
					self._lookup.append(cmd_type)

		queue.Queue._put(self, item)

	def _get(self):
		if self._peekedItem is None:
			item = queue.Queue._get(self)
		else:
			item = self._peekedItem
			self._peekedItem = None

		if isinstance(item, tuple) and len(item) == 3:
			cmd, line, cmd_type = item
			if cmd_type is not None and cmd_type in self._lookup:
				self._lookup.remove(cmd_type)

		return item

	def peek(self):
		if self._peekedItem is None:
			self._peekedItem = self._get()
		return self._peekedItem

class TypeAlreadyInQueue(Exception):
	def __init__(self, t, *args, **kwargs):
		Exception.__init__(self, *args, **kwargs)
		self.type = t


def get_new_timeout(type):
	now = time.time()
	return now + get_interval(type)


def get_interval(type):
	if type not in default_settings["serial"]["timeout"]:
		return 0
	else:
		return settings().getFloat(["serial", "timeout", type])

_temp_command_regex = re.compile("^M(?P<command>104|109|140|190)(\s+T(?P<tool>\d+)|\s+S(?P<temperature>[-+]?\d*\.?\d*))+")

def apply_temperature_offsets(line, offsets, current_tool=None):
	if offsets is None:
		return line

	match = _temp_command_regex.match(line)
	if match is None:
		return line

	groups = match.groupdict()
	if not "temperature" in groups or groups["temperature"] is None:
		return line

	offset = 0
	if current_tool is not None and (groups["command"] == "104" or groups["command"] == "109"):
		# extruder temperature, determine which one and retrieve corresponding offset
		tool_num = current_tool
		if "tool" in groups and groups["tool"] is not None:
			tool_num = int(groups["tool"])

		tool_key = "tool%d" % tool_num
		offset = offsets[tool_key] if tool_key in offsets and offsets[tool_key] else 0

	elif groups["command"] == "140" or groups["command"] == "190":
		# bed temperature
		offset = offsets["bed"] if "bed" in offsets else 0

	if offset == 0:
		return line

	temperature = float(groups["temperature"])
	if temperature == 0:
		return line

	return line[:match.start("temperature")] + "%f" % (temperature + offset) + line[match.end("temperature"):]

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

def process_gcode_line(line, offsets=None, current_tool=None):
	line = strip_comment(line).strip()
	if not len(line):
		return None

	if offsets is not None:
		line = apply_temperature_offsets(line, offsets, current_tool=current_tool)

	return line

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
		except:
			# invalid regex or something like this, we'll just skip this entry
			pass

	result = dict()
	for t in triggers.keys():
		if len(triggers[t]) > 0:
			result[t] = re.compile("|".join(map(lambda pattern: "({pattern})".format(pattern=pattern), triggers[t])))
	return result


def convert_feedback_controls(configured_controls):
	def preprocess_feedback_control(control, result):
		if "key" in control and "regex" in control and "template" in control:
			# key is always the md5sum of the regex
			key = control["key"]

			if result[key]["pattern"] is None or result[key]["matcher"] is None:
				# regex has not been registered
				try:
					result[key]["matcher"] = re.compile(control["regex"])
					result[key]["pattern"] = control["regex"]
				except Exception as exc:
					logging.getLogger(__name__).warn("Invalid regex {regex} for custom control: {exc}".format(regex=control["regex"], exc=str(exc)))

			result[key]["templates"][control["template_key"]] = control["template"]

		elif "children" in control:
			for c in control["children"]:
				preprocess_feedback_control(c, result)

	def prepare_result_entry():
		return dict(pattern=None, matcher=None, templates=dict())

	from collections import defaultdict
	feedback_controls = defaultdict(prepare_result_entry)

	for control in configured_controls:
		preprocess_feedback_control(control, feedback_controls)

	feedback_pattern = []
	for match_key, entry in feedback_controls.items():
		if entry["matcher"] is None or entry["pattern"] is None:
			continue
		feedback_pattern.append("(?P<group{key}>{pattern})".format(key=match_key, pattern=entry["pattern"]))
	feedback_matcher = re.compile("|".join(feedback_pattern))

	return feedback_controls, feedback_matcher

