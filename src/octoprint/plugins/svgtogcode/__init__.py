# coding=utf-8
from __future__ import absolute_import

__author__ = "Gina Häußge <osd@foosel.net>"
__license__ = "GNU Affero General Public License http://www.gnu.org/licenses/agpl.html"
__copyright__ = "Copyright (C) 2014 The OctoPrint Project - Released under terms of the AGPLv3 License"

import logging
import logging.handlers
import os
import flask
import socket


import octoprint.plugin
import octoprint.util
import octoprint.slicing
import octoprint.settings

default_settings = {
	"defaultIntensity": 500,
	"defaultFeedrate": 300,
	"debug_logging": False,
	"svgDPI": 90
}
s = octoprint.plugin.plugin_settings("svgtogcode", defaults=default_settings)

from .profile import Profile

blueprint = flask.Blueprint("plugin.svgtogcode", __name__)

@blueprint.route("/discovery.xml", methods=["GET"])
def discovery(self):
	self._logger.debug("Rendering discovery.xml")

	modelName = self._settings.get(["model", "name"])
	if not modelName:
		import octoprint.server
		modelName = "0.1"

	vendor = self._settings.get(["model", "vendor"])
	vendorUrl = self._settings.get(["model", "vendorUrl"])
	if not vendor:
		vendor = "The Mr Beam Project"
		vendorUrl = "http://www.mr-beam.org/"

	response = flask.make_response(flask.render_template("svgtogcode.xml.jinja2",
														 friendlyName=self.get_instance_name(),
														 manufacturer=vendor,
														 manufacturerUrl=vendorUrl,
														 modelName=modelName,
														 modelDescription=self._settings.get(["model", "description"]),
														 modelNumber=self._settings.get(["model", "number"]),
														 modelUrl=self._settings.get(["model", "url"]),
														 serialNumber=self._settings.get(["model", "serial"]),
														 uuid=self.get_uuid(),
														 presentationUrl=flask.url_for("index", _external=True)))
	response.headers['Content-Type'] = 'application/xml'
	return response


@blueprint.route("/import", methods=["POST"])
def importSvgToGcodeProfile():
	import datetime
	import tempfile

	from octoprint.server import slicingManager

	input_name = "file"
	input_upload_name = input_name + "." + s.globalGet(["server", "uploads", "nameSuffix"])
	input_upload_path = input_name + "." + s.globalGet(["server", "uploads", "pathSuffix"])

	if input_upload_name in flask.request.values and input_upload_path in flask.request.values:
		filename = flask.request.values[input_upload_name]
		try:
			profile_dict = Profile.from_svgtogcode_ini(flask.request.values[input_upload_path])
		except Exception as e:
			return flask.make_response("Something went wrong while converting imported profile: {message}".format(e.message), 500)

	elif input_name in flask.request.files:
		temp_file = tempfile.NamedTemporaryFile("wb", delete=False)
		try:
			temp_file.close()
			upload = flask.request.files[input_name]
			upload.save(temp_file.name)
			profile_dict = Profile.from_svgtogcode_ini(temp_file.name)
		except Exception as e:
			return flask.make_response("Something went wrong while converting imported profile: {message}".format(e.message), 500)
		finally:
			os.remove(temp_file)

		filename = upload.filename

	else:
		return flask.make_response("No file included", 400)

	if profile_dict is None:
		return flask.make_response("Could not convert Cura profile", 400)

	name, _ = os.path.splitext(filename)

	# default values for name, display name and description
	profile_name = _sanitize_name(name)
	profile_display_name = name
	profile_description = "Imported from {filename} on {date}".format(filename=filename, date=octoprint.util.getFormattedDateTime(datetime.datetime.now()))
	profile_allow_overwrite = False

	# overrides
	if "name" in flask.request.values:
		profile_name = flask.request.values["name"]
	if "displayName" in flask.request.values:
		profile_display_name = flask.request.values["displayName"]
	if "description" in flask.request.values:
		profile_description = flask.request.values["description"]
	if "allowOverwrite" in flask.request.values:
		from octoprint.server.api import valid_boolean_trues
		profile_allow_overwrite = flask.request.values["allowOverwrite"] in valid_boolean_trues

	slicingManager.save_profile("svgtogcode",
	                            profile_name,
	                            profile_dict,
	                            allow_overwrite=profile_allow_overwrite,
	                            display_name=profile_display_name,
	                            description=profile_description)

	result = dict(
		resource=flask.url_for("api.slicingGetSlicerProfile", slicer="svgtogcode", name=profile_name, _external=True),
		displayName=profile_display_name,
		description=profile_description
	)
	r = flask.make_response(flask.jsonify(result), 201)
	r.headers["Location"] = result["resource"]
	return r


class SvgToGcodePlugin(octoprint.plugin.SlicerPlugin,
                 octoprint.plugin.SettingsPlugin,
                 octoprint.plugin.TemplatePlugin,
                 octoprint.plugin.AssetPlugin,
                 octoprint.plugin.BlueprintPlugin,
                 octoprint.plugin.StartupPlugin):

	def __init__(self):
		self._logger = logging.getLogger("octoprint.plugins.svgtogcode")
		self._svgtogcode_logger = logging.getLogger("octoprint.plugins.svgtogcode.engine")

		# setup job tracking across threads
		import threading
		self._slicing_commands = dict()
		self._slicing_commands_mutex = threading.Lock()
		self._cancelled_jobs = []
		self._cancelled_jobs_mutex = threading.Lock()

	##~~ StartupPlugin API

	def on_startup(self, host, port):
		# setup our custom logger
		svgtogcode_logging_handler = logging.handlers.RotatingFileHandler(s.get_plugin_logfile_path(postfix="engine"), maxBytes=2*1024*1024)
		svgtogcode_logging_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
		svgtogcode_logging_handler.setLevel(logging.DEBUG)

		self._svgtogcode_logger.addHandler(svgtogcode_logging_handler)
		self._svgtogcode_logger.setLevel(logging.DEBUG if s.get_boolean(["debug_logging"]) else logging.CRITICAL)
		self._svgtogcode_logger.propagate = False

	##~~ BlueprintPlugin API

	@octoprint.plugin.BlueprintPlugin.route("/import", methods=["POST"])
	def import_cura_profile(self):
		import datetime
		import tempfile

		from octoprint.server import slicingManager

		input_name = "file"
		input_upload_name = input_name + "." + self._settings.global_get(["server", "uploads", "nameSuffix"])
		input_upload_path = input_name + "." + self._settings.global_get(["server", "uploads", "pathSuffix"])

		if input_upload_name in flask.request.values and input_upload_path in flask.request.values:
			filename = flask.request.values[input_upload_name]
			try:
				profile_dict = Profile.from_cura_ini(flask.request.values[input_upload_path])
			except Exception as e:
				self._logger.exception("Error while converting the imported profile")
				return flask.make_response("Something went wrong while converting imported profile: {message}".format(message=str(e)), 500)

		else:
			self._logger.warn("No profile file included for importing, aborting")
			return flask.make_response("No file included", 400)

		if profile_dict is None:
			self._logger.warn("Could not convert profile, aborting")
			return flask.make_response("Could not convert Cura profile", 400)

		name, _ = os.path.splitext(filename)

		# default values for name, display name and description
		profile_name = _sanitize_name(name)
		profile_display_name = name
		profile_description = "Imported from {filename} on {date}".format(filename=filename, date=octoprint.util.get_formatted_datetime(datetime.datetime.now()))
		profile_allow_overwrite = False

		# overrides
		if "name" in flask.request.values:
			profile_name = flask.request.values["name"]
		if "displayName" in flask.request.values:
			profile_display_name = flask.request.values["displayName"]
		if "description" in flask.request.values:
			profile_description = flask.request.values["description"]
		if "allowOverwrite" in flask.request.values:
			from octoprint.server.api import valid_boolean_trues
			profile_allow_overwrite = flask.request.values["allowOverwrite"] in valid_boolean_trues

		try:
			slicingManager.save_profile("cura",
			                            profile_name,
			                            profile_dict,
			                            allow_overwrite=profile_allow_overwrite,
			                            display_name=profile_display_name,
			                            description=profile_description)
		except octoprint.slicing.ProfileAlreadyExists:
			self._logger.warn("Profile {profile_name} already exists, aborting".format(**locals()))
			return flask.make_response("A profile named {profile_name} already exists for slicer cura".format(**locals()), 409)

		result = dict(
			resource=flask.url_for("api.slicingGetSlicerProfile", slicer="cura", name=profile_name, _external=True),
			displayName=profile_display_name,
			description=profile_description
		)
		r = flask.make_response(flask.jsonify(result), 201)
		r.headers["Location"] = result["resource"]
		return r

	##~~ AssetPlugin API

	def get_assets(self):
		return dict(
			js=[ "js/convert.js", "js/working_area.js", "js/gcode_parser.js", "js/lib/snap.svg-min.js", "js/lib/photobooth_min.js", "js/matrix_oven.js", "js/render_fills.js", "js/drag_scale_rotate.js"],
			less=["less/svgtogcode.less"],
			css=["css/svgtogcode.css", "css/mrbeam.css"]
		)

	##~~ SettingsPlugin API

	def on_settings_save(self, data):
		old_debug_logging = self._settings.get_boolean(["debug_logging"])

		octoprint.plugin.SettingsPlugin.on_settings_save(self, data)

		new_debug_logging = self._settings.get_boolean(["debug_logging"])
		if old_debug_logging != new_debug_logging:
			if new_debug_logging:
				self._cura_logger.setLevel(logging.DEBUG)
			else:
				self._cura_logger.setLevel(logging.CRITICAL)

	def get_settings_defaults(self):
		return dict(
			defaultIntensity=s.get(["defaultIntensity"]),
			defaultFeedrate=s.get(["defaultFeedrate"]),
			svgDPI=s.get(["svgDPI"]),
			debug_logging=s.get_boolean(["debug_logging"])
		)

	def on_settings_save(self, data):
		if "defaultIntensity" in data and data["defaultIntensity"]:
			intensity = min(max(data["defaultIntensity"], 1), 1000)
			s.set(["defaultIntensity"], intensity)
		if "defaultFeedrate" in data and data["defaultFeedrate"]:
			feedrate = max(1,data["defaultFeedrate"])
			s.set(["defaultFeedrate"], feedrate)
		if "svgDPI" in data and data["svgDPI"]:
			s.set(["svgDPI"], data["svgDPI"])
		if "debug_logging" in data:
			old_debug_logging = s.get_boolean(["debug_logging"])
			new_debug_logging = data["debug_logging"] in octoprint.settings.valid_boolean_trues
			if old_debug_logging != new_debug_logging:
				if new_debug_logging:
					self._svgtogcode_logger.setLevel(logging.DEBUG)
				else:
					self._svgtogcode_logger.setLevel(logging.CRITICAL)
			s.set_boolean(["debug_logging"], new_debug_logging)

	##~~ TemplatePlugin API

	def get_template_configs(self):
		return [
			dict(type = 'settings', name = "Svg Conversion", template='svgtogcode_settings.jinja2', custom_bindings = False),
			dict(type = 'generic', name = "Svg Conversion", custom_bindings = False)
		]

	##~~ SlicerPlugin API

	def is_slicer_configured(self):
		# svgtogcode_engine = s.get(["svgtogcode_engine"])
		# return svgtogcode_engine is not None and os.path.exists(svgtogcode_engine)
		return True

	def get_slicer_properties(self):
		return dict(
			type="svgtogcode",
			name="svgtogcode",
			same_device=True,
			progress_report=True
		)

	def get_slicer_default_profile(self):
		path = self._settings.get(["default_profile"])
		if not path:
			path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "profiles", "default.profile.yaml")
		return self.get_slicer_profile(path)

	def get_slicer_profile(self, path):
		profile_dict = self._load_profile(path)

		display_name = None
		description = None
		if "_display_name" in profile_dict:
			display_name = profile_dict["_display_name"]
			del profile_dict["_display_name"]
		if "_description" in profile_dict:
			description = profile_dict["_description"]
			del profile_dict["_description"]

		properties = self.get_slicer_properties()
		return octoprint.slicing.SlicingProfile(properties["type"], "unknown", profile_dict, display_name=display_name, description=description)

	def save_slicer_profile(self, path, profile, allow_overwrite=True, overrides=None):
		if os.path.exists(path) and not allow_overwrite:
			raise octoprint.slicing.ProfileAlreadyExists("cura", profile.name)

		new_profile = Profile.merge_profile(profile.data, overrides=overrides)

		if profile.display_name is not None:
			new_profile["_display_name"] = profile.display_name
		if profile.description is not None:
			new_profile["_description"] = profile.description

		self._save_profile(path, new_profile, allow_overwrite=allow_overwrite)

	def do_slice(self, model_path, printer_profile, machinecode_path=None, profile_path=None, position=None, on_progress=None, on_progress_args=None, on_progress_kwargs=None):
		if not profile_path:
			profile_path = s.get(["default_profile"])
		if not machinecode_path:
			path, _ = os.path.splitext(model_path)
			machinecode_path = path + ".gco"

		self._svgtogcode_logger.info("### Slicing %s to %s using profile stored at %s" % (model_path, machinecode_path, profile_path))

		## direct call
		from os.path import expanduser
		homedir = expanduser("~")
		converter_path = homedir+"/mrbeam-inkscape-ext"

		hostname = socket.gethostname()
		if("Bucanero" in hostname):
			converter_path = '/home/teja/workspace/mrbeam-inkscape-ext'
		elif("denkbrett" in hostname):
			converter_path = '/home/flo/mrbeam/git/mrbeam-inkscape-ext'
		elif ("clems-Air" in hostname):
			converter_path = '/Users/clem/Dropbox/mrBeam/mrbeam-inkscape-ext'

		import sys
		sys.path.append(converter_path)
		from mrbeam import Laserengraver

		profile = Profile(self._load_profile(profile_path))
		params = profile.convert_to_engine2()

		dest_dir, dest_file = os.path.split(machinecode_path)
		params['directory'] = dest_dir
		params['file'] = dest_file
		params['noheaders'] = "true" # TODO... booleanify

		params['fill_areas'] = False # disabled as highly experimental
		if(s.get(["debug_logging"])):
			log_path = homedir + "/.octoprint/logs/svgtogcode.log"
			params['log_filename'] = log_path
		else:
			params['log_filename'] = ''

		try:
			engine = Laserengraver(params, model_path)
			engine.affect(on_progress, on_progress_args, on_progress_kwargs)

			self._svgtogcode_logger.info("### Conversion finished")
			return True, None # TODO add analysis about out of working area, ignored elements, invisible elements, text elements
		except octoprint.slicing.SlicingCancelled as e:
			raise e
		except Exception as e:
			print e.__doc__
			print e.message
			self._logger.exception("Conversion error ({0}): {1}".format(e.__doc__, e.message))
			return False, "Unknown error, please consult the log file"

		finally:
			with self._cancelled_jobs_mutex:
				if machinecode_path in self._cancelled_jobs:
					self._cancelled_jobs.remove(machinecode_path)
			with self._slicing_commands_mutex:
				if machinecode_path in self._slicing_commands:
					del self._slicing_commands[machinecode_path]

			self._svgtogcode_logger.info("-" * 40)

#			## shell call
#			engine_settings = self._convert_to_engine(profile_path)
#
#			from os.path import expanduser
#			homedir = expanduser("~")
#			executable = homedir + "/mrbeam-inkscape-ext/mrbeam.py"
#			log_path = homedir + "/.octoprint/logs/svgtogcode.log"
#			log_enabled = s.get(["debug_logging"])
#
#			# debugging stuff. TODO remove
#			hostname = socket.gethostname()
#			if("Bucanero" in hostname):
#				executable = homedir + "/workspace/mrbeam-inkscape-ext/mrbeam.py"
#
#			# executable = s.get(["svgtogcode_engine"])
#
#			if not executable:
#				return False, "Path to SVG converter is not configured "
#
#			dest_dir, dest_file = os.path.split(machinecode_path)
#			working_dir, _ = os.path.split(executable)
#			args = ['python "%s"' % executable, '-f "%s"' % dest_file, '-d "%s"' % dest_dir]
#			args += ['--no-header=True']
#			for k, v in engine_settings.items():
#				args += ['"%s=%s"' % (k, str(v))]
#			fill_enabled = False # disabled as highly experimental
#			if(fill_enabled):
#				args += ['--fill-areas']
#			if(log_enabled):
#				args += ['"--log-filename=%s"' % log_path]
#			args += ['"%s"' % model_path]
#
#
#			import sarge
#			command = " ".join(args)
#			self._logger.info("Running %r" % (command))
#			try:
#				p = sarge.run(command, cwd=working_dir, async=True, stdout=sarge.Capture(), stderr=sarge.Capture())
#				p.wait_events()
#				try:
#					with self._slicing_commands_mutex:
#						self._slicing_commands[machinecode_path] = p.commands[0]
#
#					line_seen = False
#					while p.returncode is None:
#						line = p.stdout.readline(timeout=0.5)
#						if not line:
#							if line_seen:
#								break
#							else:
#								continue
#
#						line_seen = True
#						self._svgtogcode_logger.debug(line.strip())
#
#						if on_progress is not None:
#							pass
#				finally:
#					p.close()
#
#				with self._cancelled_jobs_mutex:
#					if machinecode_path in self._cancelled_jobs:
#						self._svgtogcode_logger.info("### Cancelled")
#						raise octoprint.slicing.SlicingCancelled()
#
#
#				self._svgtogcode_logger.info("### Finished, returncode %d" % p.returncode)
#				if p.returncode == 0:
#					return True, None
#				else:
#					self._logger.warn("Could not slice, got return code %r" % p.returncode)
#					return False, "Got returncode %r" % p.returncode
#
#			except octoprint.slicing.SlicingCancelled as e:
#				raise e
#			except:
#				self._logger.exception("Could not slice, got an unknown error")
#				return False, "Unknown error, please consult the log file"
#
#			finally:
#				with self._cancelled_jobs_mutex:
#					if machinecode_path in self._cancelled_jobs:
#						self._cancelled_jobs.remove(machinecode_path)
#				with self._slicing_commands_mutex:
#					if machinecode_path in self._slicing_commands:
#						del self._slicing_commands[machinecode_path]
#
#				self._svgtogcode_logger.info("-" * 40)

	def cancel_slicing(self, machinecode_path):
		with self._slicing_commands_mutex:
			if machinecode_path in self._slicing_commands:
				with self._cancelled_jobs_mutex:
					self._cancelled_jobs.append(machinecode_path)
				self._slicing_commands[machinecode_path].terminate()
				self._logger.info("Cancelled slicing of %s" % machinecode_path)

	def _load_profile(self, path):
		import yaml
		profile_dict = dict()
		with open(path, "r") as f:
			try:
				profile_dict = yaml.safe_load(f)
			except:
				raise IOError("Couldn't read profile from {path}".format(path=path))
		return profile_dict

	def _save_profile(self, path, profile, allow_overwrite=True):
		import yaml
		with open(path, "wb") as f:
			yaml.safe_dump(profile, f, default_flow_style=False, indent="  ", allow_unicode=True)

	def _convert_to_engine(self, profile_path):
		profile = Profile(self._load_profile(profile_path))
		return profile.convert_to_engine()

def _sanitize_name(name):
	if name is None:
		return None

	if "/" in name or "\\" in name:
		raise ValueError("name must not contain / or \\")

	import string
	valid_chars = "-_.() {ascii}{digits}".format(ascii=string.ascii_letters, digits=string.digits)
	sanitized_name = ''.join(c for c in name if c in valid_chars)
	sanitized_name = sanitized_name.replace(" ", "_")
	return sanitized_name.lower()

__plugin_name__ = "svgtogcode"
__plugin_version__ = "0.1"
__plugin_implementation__ = SvgToGcodePlugin()
