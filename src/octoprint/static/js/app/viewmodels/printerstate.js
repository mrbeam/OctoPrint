$(function() {
    function PrinterStateViewModel(parameters) {
        var self = this;

        self.loginState = parameters[0];

        self.stateString = ko.observable(undefined);
        self.isErrorOrClosed = ko.observable(undefined);
        self.isOperational = ko.observable(undefined);
        self.isPrinting = ko.observable(undefined);
        self.isPaused = ko.observable(undefined);
        self.isError = ko.observable(undefined);
        self.isReady = ko.observable(undefined);
        self.isLoading = ko.observable(undefined);
        self.isSdReady = ko.observable(undefined);
	    self.isLocked = ko.observable(undefined);
	    self.isConnecting = ko.observable(undefined);
	    self.isFlashing = ko.observable(undefined);

        self.filename = ko.observable(undefined);
        self.progress = ko.observable(undefined);
        self.filesize = ko.observable(undefined);
        self.filepos = ko.observable(undefined);
        self.printTime = ko.observable(undefined);
        self.printTimeLeft = ko.observable(undefined);
        self.sd = ko.observable(undefined);
        self.timelapse = ko.observable(undefined);

        self.busyFiles = ko.observableArray([]);

        self.filament = ko.observableArray([]);
        self.estimatedPrintTime = ko.observable(undefined);
        self.lastPrintTime = ko.observable(undefined);

        self.currentHeight = ko.observable(undefined);
		self.currentPos = ko.observable(undefined);
		self.intensityOverride = ko.observable(100);
		self.feedrateOverride = ko.observable(100);
		self.intensityOverride.extend({ rateLimit: 500 });
		self.feedrateOverride.extend({ rateLimit: 500 });

        self.TITLE_PRINT_BUTTON_PAUSED = gettext("Restarts the print job from the beginning");
        self.TITLE_PRINT_BUTTON_UNPAUSED = gettext("Starts the print job");
        self.TITLE_PAUSE_BUTTON_PAUSED = gettext("Resumes the print job");
        self.TITLE_PAUSE_BUTTON_UNPAUSED = gettext("Pauses the print job");

        self.titlePrintButton = ko.observable(self.TITLE_PRINT_BUTTON_UNPAUSED);
        self.titlePauseButton = ko.observable(self.TITLE_PAUSE_BUTTON_UNPAUSED);

        self.estimatedPrintTimeString = ko.computed(function() {
            if (self.lastPrintTime())
                return formatDuration(self.lastPrintTime());
            if (self.estimatedPrintTime())
                return formatDuration(self.estimatedPrintTime());
            return "-";
        });
        self.byteString = ko.computed(function() {
            if (!self.filesize())
                return "-";
            var filepos = self.filepos() ? formatSize(self.filepos()) : "-";
            return filepos + " / " + formatSize(self.filesize());
        });
        self.heightString = ko.computed(function() {
            if (!self.currentHeight())
                return "-";
            return _.sprintf("%.02fmm", self.currentHeight());
        });
        self.printTimeString = ko.computed(function() {
            if (!self.printTime())
                return "-";
            return formatDuration(self.printTime());
        });
        self.printTimeLeftString = ko.computed(function() {
            if (self.printTimeLeft() == undefined) {
                if (!self.printTime() || !(self.isPrinting() || self.isPaused())) {
                    return "-";
                } else {
                    return gettext("Calculating...");
                }
            } else {
                return formatFuzzyEstimation(self.printTimeLeft());
            }
        });
        self.progressString = ko.computed(function() {
            if (!self.progress())
                return 0;
            return self.progress();
        });
        self.pauseString = ko.computed(function() {
            if (self.isPaused())
                return gettext("Continue");
            else
                return gettext("Pause");
        });

        self.timelapseString = ko.computed(function() {
            var timelapse = self.timelapse();

            if (!timelapse || !timelapse.hasOwnProperty("type"))
                return "-";
            var type = timelapse["type"];
            if (type == "zchange") {
                return gettext("On Z Change");
            } else if (type == "timed") {
                return gettext("Timed") + " (" + timelapse["options"]["interval"] + " " + gettext("sec") + ")";
            } else {
                return "-";
            }
        });

        self.fromCurrentData = function(data) {
            self._fromData(data);
        };

        self.fromHistoryData = function(data) {
            self._fromData(data);
        };

        self.fromTimelapseData = function(data) {
            self.timelapse(data);
        };


        self._fromData = function(data) {
            self._processStateData(data.state);
            self._processJobData(data.job);
            self._processProgressData(data.progress);
            self._processZData(data.currentZ);
            self._processBusyFiles(data.busyFiles);
            self._processWPosData(data.workPosition);
        };
        self._processWPosData = function(data) {
            if (data == null) {
                self.currentPos({x: 0, y: 0});
            } else {
                self.currentPos({x: data[0], y: data[1]});
            }
        };
        self._processStateData = function(data) {
            var prevPaused = self.isPaused();
            self.stateString(gettext(data.text));
            self.isErrorOrClosed(data.flags.closedOrError);
            self.isOperational(data.flags.operational);
            self.isPaused(data.flags.paused);
            self.isPrinting(data.flags.printing);
            self.isError(data.flags.error);
            self.isReady(data.flags.ready);
            self.isSdReady(data.flags.sdReady);
			self.isLocked(data.flags.locked);
			self.isFlashing(data.flags.flashing);
			self.isConnecting(data.text === "Connecting" || data.text === "Opening serial port");

            if (self.isPaused() != prevPaused) {
                if (self.isPaused()) {
                    self.titlePrintButton(self.TITLE_PRINT_BUTTON_PAUSED);
                    self.titlePauseButton(self.TITLE_PAUSE_BUTTON_PAUSED);
                } else {
                    self.titlePrintButton(self.TITLE_PRINT_BUTTON_UNPAUSED);
                    self.titlePauseButton(self.TITLE_PAUSE_BUTTON_UNPAUSED);
                }
            }
        };

        self._processJobData = function(data) {
            if (data.file) {
                self.filename(data.file.name);
                self.filesize(data.file.size);
                self.sd(data.file.origin == "sdcard");
            } else {
                self.filename(undefined);
                self.filesize(undefined);
                self.sd(undefined);
            }
            self.estimatedPrintTime(data.estimatedPrintTime);
            self.lastPrintTime(data.lastPrintTime);
            var result = [];
            if (data.filament && typeof(data.filament) == "object" && _.keys(data.filament).length > 0) {
                for (var key in data.filament) {
                    if (!_.startsWith(key, "tool") || !data.filament[key] || !data.filament[key].hasOwnProperty("length") || data.filament[key].length <= 0) continue;

                    result.push({
                        name: ko.observable(gettext("Tool") + " " + key.substr("tool".length)),
                        data: ko.observable(data.filament[key])
                    });
                }
            }
            self.filament(result);
        };

        self._processProgressData = function(data) {
            if (data.completion) {
                self.progress(data.completion);
            } else {
                self.progress(undefined);
            }
            self.filepos(data.filepos);
            self.printTime(data.printTime);
            self.printTimeLeft(data.printTimeLeft);
        };

        self._processZData = function(data) {
            self.currentHeight(data);
		};


		self.show_safety_glasses_warning = function (callback) {
			$('#confirmation_dialog .confirmation_dialog_message div').remove();
			jQuery('<div/>', {
				class: "safety_glasses_heads_up"
			}).appendTo("#confirmation_dialog .confirmation_dialog_message");
			jQuery('<div/>', {
				class: "safety_glasses_warning",
				text: gettext("The laser will now start. Protect yourself and everybody in the room appropriately before proceeding!")
			}).appendTo("#confirmation_dialog .confirmation_dialog_message");
			$("#confirmation_dialog .confirmation_dialog_acknowledge").unbind("click");
			$("#confirmation_dialog .confirmation_dialog_acknowledge").click(
					function (e) {
						if (typeof callback === 'function') {
							callback(e);
							$("#confirmation_dialog").modal("hide");
							$("#confirmation_dialog .confirmation_dialog_message").html('');
						}
					});
			$("#confirmation_dialog").modal("show");

		};

		self.print_with_safety_glasses_warning = function () {
			var callback = function (e) {
				e.preventDefault();
				self.print();
			};
			self.show_safety_glasses_warning(callback);
		};

        self._processBusyFiles = function(data) {
            var busyFiles = [];
            _.each(data, function(entry) {
                if (entry.hasOwnProperty("name") && entry.hasOwnProperty("origin")) {
                    busyFiles.push(entry.origin + ":" + entry.name);
                }
            });
            self.busyFiles(busyFiles);
        };

        self.print = function() {
            var restartCommand = function() {
                self._jobCommand("restart");
            };

            if (self.isPaused()) {
                $("#confirmation_dialog .confirmation_dialog_message").text(gettext("This will restart the print job from the beginning."));
                $("#confirmation_dialog .confirmation_dialog_acknowledge").unbind("click");
                $("#confirmation_dialog .confirmation_dialog_acknowledge").click(function(e) {e.preventDefault(); $("#confirmation_dialog").modal("hide"); restartCommand(); });
                $("#confirmation_dialog").modal("show");
            } else {
                self._jobCommand("start");
            }

        };

        self.pause = function() {
            self._jobCommand("pause");
        };

        self.cancel = function() {
            self._jobCommand("cancel");
        };

		self._jobCommand = function(command, callback) {
            $.ajax({
                url: API_BASEURL + "job",
                type: "POST",
                dataType: "json",
                contentType: "application/json; charset=UTF-8",
                data: JSON.stringify({command: command}),
                success: function(response) {
                    if (callback != undefined) {
                        callback();
                    }
                }
            });
        };

		self.onEventRealTimeState = function(payload){
			self.currentPos({x: payload.wx, y: payload.wy});
		};
		
		self.intensityOverride.subscribe(function(factor){
			self._overrideCommand("/intensity "+factor);
		});
		self.feedrateOverride.subscribe(function(factor){
			self._overrideCommand("/feedrate "+factor);
		});
		
		self._overrideCommand = function(command, callback) {
            $.ajax({
                url: API_BASEURL + "printer/command",
                type: "POST",
                dataType: "json",
                contentType: "application/json; charset=UTF-8",
                data: JSON.stringify({command: command}),
                success: function(response) {
                    if (callback != undefined) {
                        callback();
                    }
                }
            });
        };
		
		self._configureOverrideSliders = function() {
			self.intensityOverrideSlider = $("#intensity_override_slider").slider({
				step: 1,
				min: 10,
				max: 200,
				value: 100,
//				tooltip: 'hide'
			}).on("slideStop", function(ev){
				self.intensityOverride(ev.value);
			});
			
			self.feedrateOverrideSlider = $("#feedrate_override_slider").slider({
				step: 1,
				min: 10,
				max: 200,
				value: 100,
//				tooltip: 'hide'
			}).on("slideStop", function(ev){
				self.feedrateOverride(ev.value);
			});

		};
		
		self.onEventPrintDone = function(){
			self.feedrateOverrideSlider.slider('setValue', 100);
			self.intensityOverrideSlider.slider('setValue', 100);
			self.intensityOverride(100);
			self.feedrateOverride(100);
		};
		
		self.onStartup = function() {
			self._configureOverrideSliders();
		};
    }

    OCTOPRINT_VIEWMODELS.push([
        PrinterStateViewModel,
        ["loginStateViewModel"],
        ["#state_wrapper", "#drop_overlay"]
    ]);
});
