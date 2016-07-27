# coding=utf-8
from __future__ import absolute_import

__author__ = "Gina Häußge <osd@foosel.net>"
__license__ = 'GNU Affero General Public License http://www.gnu.org/licenses/agpl.html'
__copyright__ = "Copyright (C) 2014 The OctoPrint Project - Released under terms of the AGPLv3 License"


from . import s

import re



defaults = dict(
	# general settings
	svgDPI = 90,
	pierce_time = 0,

	# vector settings
	speed = 300,
	intensity = 500,
	fill_areas = False,
	cross_fill = False,
	fill_angle = 0,
	fill_spacing = 0.25,

	# pixel settings
	beam_diameter = 0.25,
	intensity_white = 0,
	intensity_black = 500,
	feedrate_white = 1500,
	feedrate_black = 250,
	img_contrast = 1.0,
	img_sharpening = 1.0,
	img_dithering = False
)


class Profile(object):

	regex_extruder_offset = re.compile("extruder_offset_([xy])(\d)")
	regex_filament_diameter = re.compile("filament_diameter(\d?)")
	regex_print_temperature = re.compile("print_temperature(\d?)")
	regex_strip_comments = re.compile(";.*$", flags=re.MULTILINE)

	@classmethod
	def from_svgtogcode_ini(cls, path):
		import os
		if not os.path.exists(path) or not os.path.isfile(path):
			return None

		import ConfigParser
		config = ConfigParser.ConfigParser()
		try:
			config.read(path)
		except:
			return None

		arrayified_options = ["print_temperature", "filament_diameter", "start.gcode", "end.gcode"]
		translated_options = dict(
			inset0_speed="outer_shell_speed",
			insetx_speed="inner_shell_speed",
			layer0_width_factor="first_layer_width_factor",
			simple_mode="follow_surface",
		)
		translated_options["start.gcode"] = "start_gcode"
		translated_options["end.gcode"] = "end_gcode"
		value_conversions = dict(
			platform_adhesion={
				"None": PlatformAdhesionTypes.NONE,
				"Brim": PlatformAdhesionTypes.BRIM,
				"Raft": PlatformAdhesionTypes.RAFT
			},
			support={
				"None": SupportLocationTypes.NONE,
				"Touching buildplate": SupportLocationTypes.TOUCHING_BUILDPLATE,
				"Everywhere": SupportLocationTypes.EVERYWHERE
			},
			support_type={
				"Lines": SupportTypes.LINES,
				"Grid": SupportTypes.GRID
			},
			support_dual_extrusion={
				"Both": SupportDualTypes.BOTH,
				"First extruder": SupportDualTypes.FIRST,
				"Second extruder": SupportDualTypes.SECOND
			}
		)

		result = dict()
		for section in config.sections():
			if not section in ("profile", "alterations"):
				continue

			for option in config.options(section):
				ignored = False
				key = option

				# try to fetch the value in the correct type
				try:
					value = config.getboolean(section, option)
				except:
					# no boolean, try int
					try:
						value = config.getint(section, option)
					except:
						# no int, try float
						try:
							value = config.getfloat(section, option)
						except:
							# no float, use str
							value = config.get(section, option)
				index = None

				for opt in arrayified_options:
					if key.startswith(opt):
						if key == opt:
							index = 0
						else:
							try:
								# try to convert the target index, e.g. print_temperature2 => print_temperature[1]
								index = int(key[len(opt):]) - 1
							except ValueError:
								# ignore entries for which that fails
								ignored = True
						key = opt
						break
				if ignored:
					continue

				if key in translated_options:
					# if the key has to be translated to a new value, do that now
					key = translated_options[key]

				if key in value_conversions and value in value_conversions[key]:
					value = value_conversions[key][value]

				if index is not None:
					# if we have an array to fill, make sure the target array exists and has the right size
					if not key in result:
						result[key] = []
					if len(result[key]) <= index:
						for n in xrange(index - len(result[key]) + 1):
							result[key].append(None)
					result[key][index] = value
				else:
					# just set the value if there's no array to fill
					result[key] = value

		# merge it with our default settings, the imported profile settings taking precedence
		return cls.merge_profile(result)


	@classmethod
	def merge_profile(cls, profile, overrides=None):
		import copy

		result = copy.deepcopy(defaults)
		for k in result.keys():
			profile_value = None
			override_value = None

			if k in profile:
				profile_value = profile[k]
			if overrides and k in overrides:
				override_value = overrides[k]

			if profile_value is None and override_value is None:
				# neither override nor profile, no need to handle this key further
				continue

			# just change the result value to the override_value if available, otherwise to the profile_value if
			# that is given, else just leave as is
			if override_value is not None:
				result[k] = override_value
			elif profile_value is not None:
				result[k] = profile_value
		return result

	def __init__(self, profile):
		self.profile = profile

	def get(self, key):
		if key in self.profile:
			return self.profile[key]
		elif key in defaults:
			return defaults[key]
		else:
			return None

	def get_int(self, key, default=None):
		value = self.get(key)
		if value is None:
			return default

		try:
			return int(value)
		except ValueError:
			return default

	def get_float(self, key, default=None):
		value = self.get(key)
		if value is None:
			return default

		if isinstance(value, (str, unicode, basestring)):
			value = value.replace(",", ".").strip()

		try:
			return float(value)
		except ValueError:
			return default

	def get_boolean(self, key, default=None):
		value = self.get(key)
		if value is None:
			return default

		if isinstance(value, bool):
			return value
		elif isinstance(value, (str, unicode, basestring)):
			return value.lower() == "true" or value.lower() == "yes" or value.lower() == "on" or value == "1"
		elif isinstance(value, (int, float)):
			return value > 0
		else:
			return value == True

	def get_microns(self, key, default=None):
		value = self.get_float(key, default=None)
		if value is None:
			return default
		return int(value * 1000)



	def convert_to_engine(self):

		settings = {
			"--engraving-laser-speed": self.get_int("speed"),
			"--laser-intensity": self.get_int("intensity"),
			"--beam-diameter" : self.get_float("beam_diameter"),
			"--img-intensity-white" : self.get_int("intensity_white"),
			"--img-intensity-black" : self.get_int("intensity_black"),
			"--img-speed-white" : self.get_int("feedrate_white"),
			"--img-speed-black" : self.get_int("feedrate_black"),
			"--pierce-time" : self.get_float("pierce_time"),
			"--contrast": self.get_float("img_contrast"),
			"--sharpening": self.get_float("img_sharpening"),
			"--img-dithering": self.get_boolean("img_dithering")
		}

		return settings


	def convert_to_engine2(self):

		settings = {
			"engraving_laser_speed": self.get_int("speed"),
			"laser_intensity": self.get_int("intensity"),
			"beam_diameter" : self.get_float("beam_diameter"),
			"intensity_white" : self.get_int("intensity_white"),
			"intensity_black" : self.get_int("intensity_black"),
			"speed_white" : self.get_int("feedrate_white"),
			"speed_black" : self.get_int("feedrate_black"),
			"pierce_time" : self.get_float("pierce_time"),
			"contrast": self.get_float("img_contrast"),
			"sharpening": self.get_float("img_sharpening"),
			"dithering": self.get_boolean("img_dithering"),
			"fill_areas": self.get_boolean("fill_areas"),
			"cross_fill": self.get_boolean("cross_fill"),
			"fill_angle": self.get_float("fill_angle"),
			"fill_spacing": self.get_float("fill_spacing"),
			"svgDPI": self.get_float("svgDPI")
		}

		return settings
