# coding=utf-8
from __future__ import absolute_import

__author__ = "Gina Häußge <osd@foosel.net>"
__license__ = 'GNU Affero General Public License http://www.gnu.org/licenses/agpl.html'
__copyright__ = "Copyright (C) 2014 The OctoPrint Project - Released under terms of the AGPLv3 License"


import os
import copy
import re
import logging

from octoprint.util import dict_merge, dict_clean, dict_contains_keys

class SaveError(Exception):
	pass

class CouldNotOverwriteError(SaveError):
	pass

class InvalidProfileError(Exception):
	pass

class LaserCutterProfileManager(object):

	default = dict(
		id = "_mrbeam_junior",
		name = "Mr Beam",
		model = "Junior",
		volume=dict(
			width = 217,
			depth = 298,
			height = 0,
			origin_offset_x = 1.1,
			origin_offset_y = 1.1,
		),
		zAxis = False,
		axes=dict(
			x = dict(speed=5000, inverted=False),
			y = dict(speed=5000, inverted=False),
			z = dict(speed=1000, inverted=False)
		)
	)

	def __init__(self, settings):
		self._current = None
		self.settings = settings
		self._folder = self.settings.getBaseFolder("plugins")+"/lasercutterprofiles"
		if not os.path.exists(self._folder):
			os.makedirs(self._folder)
		self._logger = logging.getLogger(__name__)

	def select(self, identifier):
		if identifier is None or not self.exists(identifier):
			self._current = self.get_default()
			return False
		else:
			self._current = self.get(identifier)
			return True

	def deselect(self):
		self._current = None

	def get_all(self):
		return self._load_all()

	def get(self, identifier):
		try:
			if identifier == "_default":
				return self._load_default()
			elif self.exists(identifier):
				return self._load_from_path(self._get_profile_path(identifier))
			else:
				return None
		except InvalidProfileError:
			return None

	def remove(self, identifier):
		if identifier == "_default":
			return False
		return self._remove_from_path(self._get_profile_path(identifier))

	def save(self, profile, allow_overwrite=False, make_default=False):
		if "id" in profile and profile['id'] != '':
			identifier = profile["id"]
		elif "name" in profile:
			identifier = profile["name"]
		else:
			raise InvalidProfileError("profile must contain either id or name")

		identifier = self._sanitize(identifier)
		profile["id"] = identifier
		profile = dict_clean(profile, self.__class__.default)

		if identifier == "_default":
			default_profile = dict_merge(self._load_default(), profile)
			if not self._ensure_valid_profile(default_profile):
				raise InvalidProfileError()

			self.settings.set(["defaultProfile"], default_profile, defaults=dict(lasercutterprofiles=dict(defaultProfile=self.__class__.default)))
			self.settings.save()
		else:
			self._save_to_path(self._get_profile_path(identifier), profile, allow_overwrite=allow_overwrite)

			if make_default:
				self.set_default(identifier)

		return self.get(identifier)

	def get_default(self):
		default = self.settings.get(["current_profile_id"])
		if default is not None and self.exists(default):
			profile = self.get(default)
			if profile is not None:
				return profile

		return self._load_default()

	def set_default(self, identifier):
		all_identifiers = self._load_all_identifiers().keys()
		if identifier is not None and not identifier in all_identifiers:
			return

		self.settings.set(["current_profile_id"], identifier)
		self.settings.save()

	def get_current_or_default(self):
		if self._current is not None:
			return self._current
		else:
			return self.get_default()

	def get_current(self):
		return self._current

	def exists(self, identifier):
		if identifier is None:
			return False
		elif identifier == "_mrbeam_junior" or identifier == "_mrbeam_senior":
			return True
		else:
			path = self._get_profile_path(identifier)
			return os.path.exists(path) and os.path.isfile(path)

	def _load_all(self):
		all_identifiers = self._load_all_identifiers()
		results = dict()
		for identifier, path in all_identifiers.items():
			try:
				profile = self._load_from_path(path)
			except InvalidProfileError:
				continue

			if profile is None:
				continue

			results[identifier] = dict_merge(self._load_default("_mrbeam_junior"), profile)
			
		results["_mrbeam_junior"] = self._load_default("_mrbeam_junior")
		results["_mrbeam_senior"] = self._load_default("_mrbeam_senior")
		return results

	def _load_all_identifiers(self):
		results = dict()
		for entry in os.listdir(self._folder):
			if entry.startswith(".") or not entry.endswith(".profile") or entry == "_default.profile":
				continue

			path = os.path.join(self._folder, entry)
			if not os.path.isfile(path):
				continue

			identifier = entry[:-len(".profile")]
			results[identifier] = path
		return results

	def _load_from_path(self, path):
		if not os.path.exists(path) or not os.path.isfile(path):
			return None

		import yaml
		with open(path) as f:
			profile = yaml.safe_load(f)
		profile = self._ensure_valid_profile(profile)
		if not profile:
			self._logger.warn("Invalid profile: %s" % path)
			raise InvalidProfileError()
		return profile

	def _save_to_path(self, path, profile, allow_overwrite=False):
		validated_profile = self._ensure_valid_profile(profile)
		if not validated_profile:
			raise InvalidProfileError()

		if os.path.exists(path) and not allow_overwrite:
			raise SaveError("Profile %s already exists and not allowed to overwrite" % profile["id"])

		import yaml
		with open(path, "wb") as f:
			try:
				yaml.safe_dump(profile, f, default_flow_style=False, indent="  ", allow_unicode=True)
			except Exception as e:
				raise SaveError("Cannot save profile %s: %s" % (profile["id"], e.message))

	def _remove_from_path(self, path):
		try:
			os.remove(path)
			return True
		except:
			return False

	def _load_default(self, defaultModel = None):
		default = copy.deepcopy(self.__class__.default)
		if(defaultModel is not None and defaultModel == "_mrbeam_senior"):
			default['volume']['width'] *= 2
			default['volume']['depth'] *= 2
			default['model'] = "Senior"
			default['id'] = "_mrbeam_senior"
		
		profile = self._ensure_valid_profile(default)
		if not profile:
			self._logger.warn("Invalid default profile after applying overrides")
			raise InvalidProfileError()
		return profile

	def _get_profile_path(self, identifier):
		return os.path.join(self._folder, "%s.profile" % identifier)

	def _sanitize(self, name):
		if name is None:
			return None

		if "/" in name or "\\" in name:
			raise ValueError("name must not contain / or \\")

		import string
		valid_chars = "-_.() {ascii}{digits}".format(ascii=string.ascii_letters, digits=string.digits)
		sanitized_name = ''.join(c for c in name if c in valid_chars)
		sanitized_name = sanitized_name.replace(" ", "_")
		return sanitized_name

	def _ensure_valid_profile(self, profile):
		# ensure all keys are present
		if not dict_contains_keys(self.default, profile):
			return False

		# conversion helper
		def convert_value(profile, path, converter):
			value = profile
			for part in path[:-1]:
				if not isinstance(value, dict) or not part in value:
					raise RuntimeError("%s is not contained in profile" % ".".join(path))
				value = value[part]

			if not isinstance(value, dict) or not path[-1] in value:
				raise RuntimeError("%s is not contained in profile" % ".".join(path))

			value[path[-1]] = converter(value[path[-1]])


		# convert ints
		for path in (("axes", "x", "speed"), ("axes", "y", "speed"), ("axes", "z", "speed")):
			try:
				convert_value(profile, path, int)
			except:
				return False

		# convert floats
		for path in (("volume", "width"), ("volume", "depth"), ("volume", "height")):
			try:
				convert_value(profile, path, float)
			except:
				return False

		# convert booleans
		for path in (("axes", "x", "inverted"), ("axes", "y", "inverted"), ("axes", "z", "inverted")):
			try:
				convert_value(profile, path, bool)
			except:
				return False

		return profile

