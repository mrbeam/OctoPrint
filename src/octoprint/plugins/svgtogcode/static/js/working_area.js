function WorkingAreaViewModel(loginStateViewModel, settingsViewModel, printerStateViewModel) {
    var self = this;

    self.loginState = loginStateViewModel;
    self.settings = settingsViewModel;
    self.state = printerStateViewModel;

    self.log = [];

    self.command = ko.observable(undefined);

    self.isErrorOrClosed = ko.observable(undefined);
    self.isOperational = ko.observable(undefined);
    self.isPrinting = ko.observable(undefined);
    self.isPaused = ko.observable(undefined);
    self.isError = ko.observable(undefined);
    self.isReady = ko.observable(undefined);
    self.isLoading = ko.observable(undefined);
	self.currentPos = ko.observable(undefined);
    self.laserPos = ko.computed(function(){
		var pos = self.currentPos();
		if(!pos){
			
			return "(?, ?)";
		} else {
			return "("+ pos.x + ", "+ pos.y + ")";
		}
	}, this);
	
	self._processPos = function(posStr) {
		// example posStr: "X: 73.0000 Y: 192.0000 Z: 0.0000"
		var parts = posStr.split(" ");
		var x = parseFloat(parts[1]).toFixed(2)
		var y = parseFloat(parts[3]).toFixed(2)
        self.currentPos({x:x, y:y});
    };
	
	self._fromData = function(data) {
		if(data.workPosition){
			self._processPos(data.workPosition);
		}
    };
	
	self.fromCurrentData = function(data) {
        self._fromData(data);
    };

	self.move_laser = function(el){
		var x = event.offsetX;
		var y = event.toElement.offsetHeight - event.offsetY;
		var command = "G0 X"+x+" Y"+y;
		$.ajax({
			url: API_BASEURL + "printer/command",
			type: "POST",
			dataType: "json",
			contentType: "application/json; charset=UTF-8",
			data: JSON.stringify({"command": command})
		});
	}
	
	self.laser_start = function(e){
		console.log("start lasering...", e);
		return false;
	};


    self.titlePrintButton = self.state.titlePrintButton;
    self.titlePauseButton = self.state.titlePauseButton;
	self.pause = self.state.pause;
	self.cancel = self.state.cancel;
	
	

//	
//	self.getLaserPos = function(){
//		console.log("foo")
//		x = self.x === undefined ? '?' : self.x;
//		y = self.y === undefined ? '?' : self.y;
//		return "x"+ x + ", y"+ y;
//	}
//	
//    self.sendCommand = function() {
//        var command = self.command();
//        if (!command) {
//            return;
//        }
//
//
//    };
//
//    self.handleKeyDown = function(event) {
//        var keyCode = event.keyCode;
//
//        if (keyCode == 38 || keyCode == 40) {
//            if (keyCode == 38 && self.cmdHistory.length > 0 && self.cmdHistoryIdx > 0) {
//                console.log("keycode 38")
//            } else if (keyCode == 40 && self.cmdHistoryIdx < self.cmdHistory.length - 1) {
//                console.log("keycode 40")
//            }
//
//            // prevent the cursor from being moved to the beginning of the input field (this is actually the reason
//            // why we do the arrow key handling in the keydown event handler, keyup would be too late already to
//            // prevent this from happening, causing a jumpy cursor)
//            return false;
//        }
//
//        // do not prevent default action
//        return true;
//    };
//
//    self.handleKeyUp = function(event) {
//        if (event.keyCode == 13) {
//            self.sendCommand();
//        }
//
//        // do not prevent default action
//        return true;
//    };
}


