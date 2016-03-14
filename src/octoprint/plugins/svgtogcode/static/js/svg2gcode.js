Snap.plugin(function (Snap, Element, Paper, global) {
  /**
   * generates and returns the svg as gcode
   *
   * @param {integer} laserSpeed : value for laser speed
   * @param {integer} laserIntensity : value for laser intensity
   * @param {integer} pierceTime : value for pierce time
   * @returns {list}
   */
  Element.prototype.toGcode = function (laserSpeed,	laserIntensity, pierceTime) {
    var gCodeList = [];

    gCodeList.push(";Generated with svg2gcode Version 0.1\n");
    gCodeList.push("G21\n");
    gCodeList.push("G1F" + laserSpeed + "\n");

    var elem = this.selectAll("path");
    for (var i = 0; i < elem.length; i++) {
      gCodeList.push(elem[i].pathStringToGCode(laserIntensity, pierceTime));
    }

    // For security reasons always append a M5 command
    gCodeList.push("\nM5");

    return gCodeList;
  }

  Element.prototype.pathStringToGCode = function (laserIntensity, pierceTime) {
    if (this.type !== "path") {
      return this;
    }
    var gcode = [];
    var startP = [0, 0];
    var lastP = [0, 0];
    var lastXstr = "";
    var lastYstr = "";
    var xStr = "";
    var yStr = "";
    var arr = Snap.parsePathString(this.realPath);
    for (var i = 0; i < arr.length; i++) {
      if (arr[i][0].toUpperCase() == "M") {
        xStr = Math.round(arr[i][1] * 100) / 100;
        yStr = Math.round(arr[i][2] * 100) / 100;
        if (xStr != lastXstr || yStr != lastYstr) {
          gcode.push("M3S0");
          gcode.push("G0X" + xStr + "Y" + yStr);
          gcode.push("M3S" + laserIntensity);
          if (pierceTime != 0) {
            gcode.push("G4P" + pierceTime);
          }
          lastXstr = arr[i][1];
          lastYstr = arr[i][2];
        }
        startP = [arr[i][1], arr[i][2]];
        lastP = [arr[i][1], arr[i][2]];
      } else if (arr[i][0].toUpperCase() == "L") {
        xStr = Math.round(arr[i][1] * 100) / 100;
        yStr = Math.round(arr[i][2] * 100) / 100;
        if (xStr != lastXstr || yStr != lastYstr) {
          gcode.push("G1X" + xStr + "Y" + yStr);
          lastP = [arr[i][1], arr[i][2]];
          }
      } else if (arr[i][0].toUpperCase() == "H") {
        gcode.push("H");
      } else if (arr[i][0].toUpperCase() == "V") {
        gcode.push("V");
      } else if (arr[i][0].toUpperCase() == "C") {
        var x0 = lastP[0];
        var y0 = lastP[1];
        var x1 = arr[i][1];
        var y1 = arr[i][2];
        var x2 = arr[i][3];
        var y2 = arr[i][4];
        var x3 = arr[i][5];
        var y3 = arr[i][6];
        var tmp = Snap.path.getTotalLength("M" + lastP[0] + "," + lastP[1] + arr[i][0] + arr[i][1] + "," + arr[i][2] + "," + arr[i][3] + "," + arr[i][4] + "," + arr[i][5] + "," + arr[i][6]);
        var range = Math.round(tmp) * 10;
        for (var t = 1; t <= range; t++) {
          obj = Snap.path.findDotsAtSegment(x0, y0, x1, y1, x2, y2, x3, y3, t / range)
          xStr = Math.round(obj.x * 100) / 100;
          yStr = Math.round(obj.y * 100) / 100;
          if (xStr != lastXstr || yStr != lastYstr) {
            gcode.push("G1X" + xStr + "Y" + yStr);
          }
          lastXstr = xStr;
          lastYstr = yStr;
        }
        lastP = [arr[i][5], arr[i][6]];
      } else if (arr[i][0].toUpperCase() == "Q") {
        gcode.push("NOT_IMPLEMENTED");
      } else if (arr[i][0].toUpperCase() == "A") {
        gcode.push("NOT_IMPLEMENTED");
      } else if (arr[i][0].toUpperCase() == "Z") {
        if (lastP[0] != startP[0] || lastP[1] != startP[1]) {
          xStr = Math.round(startP[0] * 100) / 100;
          yStr = Math.round(startP[1] * 100) / 100;
          if (xStr != lastXstr || yStr != lastYstr) {
            gcode.push("G1X" + xStr + "Y" + yStr);
            lastXstr = xStr;
            lastYstr = yStr;
          }
        }
      }
    }
    //console.log(gcode.join("\n").length)
    return gcode.join("\n");
  }
});
