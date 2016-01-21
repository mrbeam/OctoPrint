$(function(){

	function VectorConversionViewModel(params) {
		var self = this;

		self.loginState = params[0];
		self.settings = params[1];
		self.state = params[2];
		self.workingArea = params[3];
		self.files = params[4];

		self.target = undefined;
		self.file = undefined;
		self.data = undefined;
		self.slicing_progress = ko.observable(0);
		self.slicing_in_progress = ko.observable(false);

		self.title = ko.observable(undefined);
		self.slicer = ko.observable();
		self.slicers = ko.observableArray();
		self.profile = ko.observable();
		self.profiles = ko.observableArray();
		self.defaultSlicer = undefined;
		self.defaultProfile = undefined;
		
		// expert settings
		self.showHints = ko.observable(false);
		self.showExpertSettings = ko.observable(false);
		self.gcodeFilename = ko.observable();
		self.pierceTime = ko.observable(0);

		// vector settings
		self.show_vector_parameters = ko.observable(true);
		self.laserIntensity = ko.observable(undefined);
		self.laserSpeed = ko.observable(undefined);
		self.maxSpeed = ko.observable(3000);
		self.minSpeed = ko.observable(20);
		self.fill_areas = ko.observable(false);
		self.show_fill_areas_checkbox = ko.observable(false);
		
		// image engraving stuff
		// preset values are a good start for wood engraving
		self.images_placed = ko.observable(false);
		self.show_image_parameters = ko.computed(function(){
			return self.images_placed() || (self.fill_areas() && self.show_vector_parameters());
		});
		self.imgIntensityWhite = ko.observable(0);
		self.imgIntensityBlack = ko.observable(500);
		self.imgFeedrateWhite = ko.observable(1500); 
		self.imgFeedrateBlack = ko.observable(250);
		self.imgDithering = ko.observable(false);
		self.imgSharpening = ko.observable(1);
		self.imgContrast = ko.observable(1);
		self.beamDiameter = ko.observable(0.2);
		
		self.sharpeningMax = 25;
		self.contrastMax = 2;
		
		// preprocessing preview ... returns opacity 0.0 - 1.0
		self.sharpenedPreview = ko.computed(function(){
			if(self.imgDithering()) return 0;
			else {
				var sharpeningPercents = (self.imgSharpening() - 1)/(self.sharpeningMax - 1);
				var contrastPercents = (self.imgContrast() - 1)/(self.contrastMax - 1);
				return sharpeningPercents - contrastPercents/2;
			}
		}, self);
		self.contrastPreview = ko.computed(function(){
			if(self.imgDithering()) return 0;
			else {
				var sharpeningPercents = (self.imgSharpening() - 1)/(self.sharpeningMax - 1);
				var contrastPercents = (self.imgContrast() - 1)/(self.contrastMax - 1);
				return contrastPercents - sharpeningPercents/2;
			}
		}, self);
		

		self.maxSpeed.subscribe(function(val){
			self._configureFeedrateSlider();
		});

		// shows conversion dialog and extracts svg first
		self.show_conversion_dialog = function() {
			self.gcodeFilesToAppend = self.workingArea.getPlacedGcodes();
			self.show_vector_parameters(self.workingArea.getPlacedSvgs().length > 0);
			self.show_fill_areas_checkbox(self.workingArea.hasFilledVectors())
			self.images_placed(self.workingArea.getPlacedImages().length > 0);
			//self.show_image_parameters(self.workingArea.getPlacedImages().length > 0);

			if(self.show_vector_parameters() || self.show_image_parameters()){
				if(self.laserIntensity() === undefined){
					var intensity = self.settings.settings.plugins.svgtogcode.defaultIntensity();
					self.laserIntensity(intensity);
				} 
				if(self.laserSpeed() === undefined){
					var speed = self.settings.settings.plugins.svgtogcode.defaultFeedrate();
					self.laserSpeed(speed);
				}

				var gcodeFile = self.create_gcode_filename(self.workingArea.placedDesigns());
				self.gcodeFilename(gcodeFile);

				self.title(gettext("Converting"));
				$("#dialog_vector_graphics_conversion").modal("show"); // calls self.convert() afterwards
			} else {
				// just gcodes were placed. Start lasering right away.
				self.convert();
			}
		};
		
		self.cancel_conversion = function(){
			if(self.slicing_in_progress()){
				//console.log('cancel slicing', self.slicing_in_progress());
				// TODO cancel slicing properly
			}
		};

		self.create_gcode_filename = function(placedDesigns){
			if(placedDesigns.length > 0){
				var filemap = {};
				for(var idx in placedDesigns){
					var design = placedDesigns[idx];
					var end = design.name.lastIndexOf('.');
					var name = design.name.substring(0, end);
					if(filemap[name] !== undefined) filemap[name] += 1;
					else filemap[name] = 1;
				}
				var mostPlaced;
				var placed = 0;
				for(var name in filemap){
					if(filemap[name] > placed){
						mostPlaced = name;
						placed = filemap[name];
					}
				}
				var uniqueDesigns = Object.keys(filemap).length;
				var gcode_name = mostPlaced;
				if(placed > 1) gcode_name += "." + placed + "x";
				if(uniqueDesigns > 1){
					gcode_name += "_"+(uniqueDesigns-1)+"more";
				}
				
				return gcode_name;
			} else { 
				console.error("no designs placed.");
				return;
			}
		};
		
		self.settingsString = ko.computed(function(){
			var intensity = self.laserIntensity();
			var feedrate = self.laserSpeed();
			var settingsString = "_i" + intensity + "s" + Math.round(feedrate);
			return settingsString;
		});

		self.slicer.subscribe(function(newValue) {
			self.profilesForSlicer(newValue);
		});

		self.enableConvertButton = ko.computed(function() {
			if (self.slicing_in_progress() || self.laserIntensity() === undefined || self.laserSpeed() === undefined || self.gcodeFilename() === undefined) {
				return false;
			} else {
				var tmpIntensity = self.laserIntensity();
				var tmpSpeed = self.laserSpeed();
				var tmpGcodeFilename = self.gcodeFilename().trim();
				return tmpGcodeFilename !== ""
					&& tmpIntensity > 0 && tmpIntensity <= 1000 // TODO no magic numbers here!
					&& tmpSpeed >= self.minSpeed() && tmpSpeed <= self.maxSpeed();
			}
		});

		self.requestData = function() {
			$.ajax({
				url: API_BASEURL + "slicing",
				type: "GET",
				dataType: "json",
				success: self.fromResponse
			});
		};

		self.fromResponse = function(data) {
			self.data = data;

			var selectedSlicer = undefined;
			self.slicers.removeAll();
			_.each(_.values(data), function(slicer) {
				var name = slicer.displayName;
				if (name === undefined) {
					name = slicer.key;
				}

				if (slicer.default) {
					selectedSlicer = slicer.key;
				}

				self.slicers.push({
					key: slicer.key,
					name: name
				});
			});

			if (selectedSlicer !== undefined) {
				self.slicer(selectedSlicer);
			}

			self.defaultSlicer = selectedSlicer;
		};

		self.profilesForSlicer = function(key) {
			if (key === undefined) {
				key = self.slicer();
			}
			if (key === undefined || !self.data.hasOwnProperty(key)) {
				return;
			}
			var slicer = self.data[key];

			var selectedProfile = undefined;
			self.profiles.removeAll();
			_.each(_.values(slicer.profiles), function(profile) {
				var name = profile.displayName;
				if (name === undefined) {
					name = profile.key;
				}

				if (profile.default) {
					selectedProfile = profile.key;
				}

				self.profiles.push({
					key: profile.key,
					name: name
				});
			});

			if (selectedProfile !== undefined) {
				self.profile(selectedProfile);
			}

			self.defaultProfile = selectedProfile;
		};

		self.convert = function() {
			if(self.gcodeFilesToAppend.length === 1 && self.svg === undefined){
				self.files.startGcodeWithSafetyWarning(self.gcodeFilesToAppend[0]);
			} else {
				self.slicing_in_progress(true);
				self.workingArea.getCompositionSVG(self.fill_areas(), function(composition){
					self.svg = composition;	
					var filename = self.gcodeFilename() + self.settingsString() + '.gco';
					var gcodeFilename = self._sanitize(filename);

					var data = {
						command: "convert",
						"profile.speed": self.laserSpeed(),
						"profile.intensity": self.laserIntensity(),
						"profile.fill_areas": self.fill_areas(),
						"profile.pierce_time": self.pierceTime(),
						"profile.intensity_black" : self.imgIntensityBlack(),
						"profile.intensity_white" : self.imgIntensityWhite(),
						"profile.feedrate_black" : self.imgFeedrateBlack(),
						"profile.feedrate_white" : self.imgFeedrateWhite(),
						"profile.img_contrast" : self.imgContrast(),
						"profile.img_sharpening" : self.imgSharpening(),
						"profile.img_dithering" : self.imgDithering(),
						"profile.beam_diameter" : self.beamDiameter(),
						slicer: "svgtogcode",
						gcode: gcodeFilename
					};

					if(self.svg !== undefined){
						data.svg = self.svg;
					} else {
						data.svg = '<svg height="0" version="1.1" width="0" xmlns="http://www.w3.org/2000/svg"><defs/></svg>';
					}
					if(self.gcodeFilesToAppend !== undefined){
						data.gcodeFilesToAppend = self.gcodeFilesToAppend;
					}

					$.ajax({
						url: API_BASEURL + "files/convert",
						type: "POST",
						dataType: "json",
						contentType: "application/json; charset=UTF-8",
						data: JSON.stringify(data)
					});

				});
			}
		};

		self._sanitize = function(name) {
			return name.replace(/[^a-zA-Z0-9\-_\.\(\) ]/g, "").replace(/ /g, "_");
		};

		self.onStartup = function() {
			self.requestData();
			self.state.conversion = self; // hack! injecting method to avoid circular dependency.
			self.files.conversion = self;
			self._configureIntensitySlider();
			self._configureFeedrateSlider();
			self._configureImgSliders();
		};
		
		self.onSlicingProgress = function(slicer, model_path, machinecode_path, progress){
			self.slicing_progress(progress);
		};
		self.onEventSlicingStarted = function(payload){
			self.slicing_in_progress(true);
		};
		self.onEventSlicingDone = function(payload){
			// payload
//			gcode: "ex_11more_i1000s300.gco"
//			gcode_location: "local"
//			stl: "local/ex_11more_i1000s300.svg"
//			time: 30.612739086151123
			self.gcodeFilename(undefined);
			self.svg = undefined;
			$("#dialog_vector_graphics_conversion").modal("hide");
			self.slicing_in_progress(false);
			//console.log("onSlicingDone" , payload);
		};
		self.onEventSlicingCancelled = function(payload){
			self.gcodeFilename(undefined);
			self.svg = undefined;
			self.slicing_in_progress(false);
			$("#dialog_vector_graphics_conversion").modal("hide");
			//console.log("onSlicingCancelled" , payload);
		};
		self.onEventSlicingFailed = function(payload){
			self.slicing_in_progress(false);
			//console.log("onSlicingFailed" , payload);
		};

		self._configureIntensitySlider = function() {
			self.intensitySlider = $("#svgtogcode_intensity_slider").slider({
				id: "svgtogcode_intensity_slider_impl",
				reversed: false,
				selection: "after",
				orientation: "horizontal",
				min: 1,
				max: 1000,
				step: 1,
				value: 500,
				enabled: true,
				formatter: function(value) { return "" + (value/10) +"%"; }
			}).on("slideStop", function(ev){
				self.laserIntensity(ev.value);
			});

			self.laserIntensity.subscribe(function(newVal){
				self.intensitySlider.slider('setValue', parseInt(newVal));
			});
		};

		self._configureFeedrateSlider = function() {
			self.feedrateSlider = $("#svgtogcode_feedrate_slider").slider({
				id: "svgtogcode_feedrate_slider_impl",
				reversed: false,
				selection: "after",
				orientation: "horizontal",
				min: 0,
				max: 100, // fixed values to avoid reinitializing after profile changes
				step: 1,
				value: 300,
				enabled: true,
				formatter: function(value) { return "" + Math.round(self._calcRealSpeed(value)) +"mm/min"; }
			});

			// use the class as a flag to avoid double binding of the slideStop event
			if($("#svgtogcode_feedrate_slider").attr('class') === 'uninitialized'){ // somehow hasClass(...) did not work ???
				self.feedrateSlider.on("slideStop", function(ev){
					$('#svgtogcode_feedrate').val(self._calcRealSpeed(ev.value));
					self.laserSpeed(self._calcRealSpeed(ev.value));
				});
				$("#svgtogcode_feedrate_slider").removeClass('uninitialized');
			}

			var speedSubscription = self.laserSpeed.subscribe(function(fromSettings){
				var realVal = parseInt(fromSettings);
				var val = 100*(realVal - self.minSpeed()) / (self.maxSpeed() - self.minSpeed());
				self.feedrateSlider.slider('setValue', val);
				//speedSubscription.dispose(); // only do it once
			});
		};

		self._calcRealSpeed = function(sliderVal){
			return Math.round(self.minSpeed() + sliderVal/100 * (self.maxSpeed() - self.minSpeed()));
		};
		
		self._configureImgSliders = function() {
			self.contrastSlider = $("#svgtogcode_contrast_slider").slider({
				step: .1,
				min: 1,
				max: self.contrastMax,
				value: 1,
				tooltip: 'hide'
			}).on("slide", function(ev){
				self.imgContrast(ev.value);
			});
			
			self.sharpeningSlider = $("#svgtogcode_sharpening_slider").slider({
				step: 1,
				min: 1,
				max: self.sharpeningMax,
				value: 1,
				class: 'img_slider',
				tooltip: 'hide'
			}).on("slide", function(ev){
				self.imgSharpening(ev.value);
			});

		};

		self.showExpertSettings.subscribe(function(){
			$('#dialog_vector_graphics_conversion').trigger('resize');
		});

	}
	
    ADDITIONAL_VIEWMODELS.push([VectorConversionViewModel, 
		["loginStateViewModel", "settingsViewModel", "printerStateViewModel", "workingAreaViewModel", "gcodeFilesViewModel"], 
		document.getElementById("dialog_vector_graphics_conversion")]);
	
});
