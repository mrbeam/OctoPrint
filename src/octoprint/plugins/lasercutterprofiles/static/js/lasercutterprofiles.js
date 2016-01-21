$(function() {
    
	function LaserCutterProfilesViewModel(params) {
    var self = this;
	
	self.workingarea = params[0];
	self.control = params[1];
	self.conversion = params[2];

    self._cleanProfile = function() {
        return {
            id: "",
            name: "",
            model: "",
            volume: {
                formFactor: "rectangular",
                width: 216,
                depth: 297,
                height: 0,
				origin_offset_x: 1,
				origin_offset_y: 1
            },
            zAxis: false,
            axes: {
                x: {speed: 6000, inverted: false},
                y: {speed: 6000, inverted: false},
                z: {speed: 200, inverted: false}
            }
        };
    };

    self.profiles = new ItemListHelper(
        "laserCutterProfiles",
        {
            "name": function(a, b) {
                // sorts ascending
                if (a["name"].toLocaleLowerCase() < b["name"].toLocaleLowerCase()) return -1;
                if (a["name"].toLocaleLowerCase() > b["name"].toLocaleLowerCase()) return 1;
                return 0;
            }
        },
        {},
        "name",
        [],
        [],
        10
    );
    self.defaultProfile = ko.observable();
    self.currentProfile = ko.observable();

    self.currentProfileData = ko.observable(ko.mapping.fromJS(self._cleanProfile()));

    self.editorNew = ko.observable(false);

    self.editorName = ko.observable();
    self.editorIdentifier = ko.observable();
    self.editorModel = ko.observable();

    self.editorVolumeWidth = ko.observable();
    self.editorVolumeDepth = ko.observable();
    self.editorVolumeHeight = ko.observable();

    self.editorZAxis = ko.observable();

    self.editorAxisXSpeed = ko.observable();
    self.editorAxisYSpeed = ko.observable();
    self.editorAxisZSpeed = ko.observable();

    self.editorAxisXInverted = ko.observable(false);
    self.editorAxisYInverted = ko.observable(false);
    self.editorAxisZInverted = ko.observable(false);

    self.makeDefault = function(data) {
        var profile = {
            id: data.id,
            default: true
        };

        self.updateProfile(profile);
    };

    self.requestData = function() {
        $.ajax({
            url: BASEURL + "plugin/lasercutterprofiles/profiles",
            type: "GET",
            dataType: "json",
            success: self.fromResponse
        })
    };

    self.fromResponse = function(data) {
        var items = [];
        var defaultProfile = undefined;
        var currentProfile = undefined;
        var currentProfileData = undefined;
        _.each(data.profiles, function(entry) {
            if (entry.default) {
                defaultProfile = entry.id;
            }
            if (entry.current) {
                currentProfile = entry.id;
                currentProfileData = ko.mapping.fromJS(entry, self.currentProfileData);
            }
            entry["isdefault"] = ko.observable(entry.default);
            entry["iscurrent"] = ko.observable(entry.current);
            items.push(entry);
        });
        self.profiles.updateItems(items);
        self.defaultProfile(defaultProfile);
        self.currentProfile(currentProfile);
        self.currentProfileData(currentProfileData);
		
		self.workingarea.workingAreaWidthMM(self.currentProfileData().volume.width() - self.currentProfileData().volume.origin_offset_x());
		self.workingarea.workingAreaHeightMM(self.currentProfileData().volume.depth() - self.currentProfileData().volume.origin_offset_y());
		var maxSpeed = Math.min(self.currentProfileData().axes.x.speed(), self.currentProfileData().axes.y.speed());
		self.conversion.maxSpeed(maxSpeed);
    };

    self.addProfile = function(callback) {
        var profile = self._editorData();
        $.ajax({
            url: BASEURL + "plugin/lasercutterprofiles/profiles",
            type: "POST",
            dataType: "json",
            contentType: "application/json; charset=UTF-8",
            data: JSON.stringify({profile: profile}),
            success: function() {
                if (callback !== undefined) {
                    callback();
                }
                self.requestData();
            }
        });
    };

    self.removeProfile = function(data) {
        $.ajax({
            url: data.resource,
            type: "DELETE",
            dataType: "json",
            success: self.requestData
        })
    };

    self.updateProfile = function(profile, callback) {
        if (profile == undefined) {
            profile = self._editorData();
        }
		
        $.ajax({
            url: BASEURL + "plugin/lasercutterprofiles/profiles/" + profile.id,
            type: "PATCH",
            dataType: "json",
            contentType: "application/json; charset=UTF-8",
            data: JSON.stringify({profile: profile}),
            success: function() {
                if (callback !== undefined) {
                    callback();
                }
                self.requestData();
            }
        });
    };

    self.showEditProfileDialog = function(data) {
        var add = false;
        if (data == undefined) {
            data = self._cleanProfile();
            add = true;
        }

        self.editorNew(add);

        self.editorIdentifier(data.id);
        self.editorName(data.name);
        self.editorModel(data.model);

        self.editorVolumeWidth(data.volume.width);
        self.editorVolumeDepth(data.volume.depth);
        self.editorVolumeHeight(data.volume.height);

        self.editorZAxis(data.zAxis);

        self.editorAxisXSpeed(data.axes.x.speed);
        self.editorAxisXInverted(data.axes.x.inverted);
        self.editorAxisYSpeed(data.axes.y.speed);
        self.editorAxisYInverted(data.axes.y.inverted);
        self.editorAxisZSpeed(data.axes.z.speed);
        self.editorAxisZInverted(data.axes.z.inverted);

        var editDialog = $("#settings_laserCutterProfiles_editDialog");
        var confirmButton = $("button.btn-confirm", editDialog);
        var dialogTitle = $("h3.modal-title", editDialog);

        dialogTitle.text(add ? gettext("Add Profile") : _.sprintf(gettext("Edit Profile \"%(name)s\""), {name: data.name}));
        confirmButton.unbind("click");
        confirmButton.bind("click", function() {
            self.confirmEditProfile(add);
        });
        editDialog.modal("show");
    };

    self.confirmEditProfile = function(add) {
        var callback = function() {
            $("#settings_laserCutterProfiles_editDialog").modal("hide");
        };

        if (add) {
            self.addProfile(callback);
        } else {
            self.updateProfile(undefined, callback);
        }
    };
	
	self.isReadOnly = function (data){
		return (data.id === "_mrbeam_senior" || data.id === "_mrbeam_junior"); 
	};

    self._editorData = function() {
        var profile = {
            id: self.editorIdentifier(),
            name: self.editorName(),
            model: self.editorModel(),
            volume: {
                width: parseFloat(self.editorVolumeWidth()),
                depth: parseFloat(self.editorVolumeDepth()),
                height: parseFloat(self.editorVolumeHeight()),
            },
            zAxis: self.editorZAxis(),
            axes: {
                x: {
                    speed: parseInt(self.editorAxisXSpeed()),
                    inverted: self.editorAxisXInverted()
                },
                y: {
                    speed: parseInt(self.editorAxisYSpeed()),
                    inverted: self.editorAxisYInverted()
                },
                z: {
                    speed: parseInt(self.editorAxisZSpeed()),
                    inverted: self.editorAxisZInverted()
                }
            }
        };


        return profile;
    };

    self.onSettingsShown = self.requestData;
    self.onStartup = function(){
		self.requestData();
		self.control.showZAxis = ko.computed(function(){
			var has = self.currentProfileData()['zAxis']();
			return has;
		}); // dependency injection
	};
	
}

	
    // view model class, identifier, parameters for constructor, container to bind to
    ADDITIONAL_VIEWMODELS.push([LaserCutterProfilesViewModel,
		["workingAreaViewModel", "controlViewModel", "vectorConversionViewModel"], 
		document.getElementById("laserCutterProfiles")]);
	
});
