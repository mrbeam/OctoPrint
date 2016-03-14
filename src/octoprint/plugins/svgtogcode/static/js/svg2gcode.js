Snap.plugin(function (Snap, Element, Paper, global) {
  /**
   * generates and returns the svg as gcode
   *
   * @param {integer} variablename : description
   * @returns {list of gcode}
   */
  Element.prototype.toGcode = function () {
    var gCodeList = [];
    var elem = this.selectAll("path");
    for (var i = 0; i < elem.length; i++) {
      gCodeList.push(elem[i].pathStringToGCode());
    }
    return gCodeList;
  }

  Element.prototype.pathStringToGCode = function () {
    if (this.type !== "path") {
      return this;
    }
    var gcode = []
    var startP = [0, 0]
    var lastP = [0, 0]
    var arr = Snap.parsePathString(this.realPath);
    for (var i = 0; i < arr.length; i++) {
      if (arr[i][0] == "M") {
        gcode.push("G0X" + arr[i][1] + "Y" + arr[i][2]);
        startP = [arr[i][1], arr[i][2]];
        lastP = [arr[i][1], arr[i][2]];
      } else if (arr[i][0] == "L") {
        gcode.push("G1X" + arr[i][1] + "Y" + arr[i][2]);
        lastP = [arr[i][1], arr[i][2]];
      } else if (arr[i][0] == "H") {
        gcode.push("H");
      } else if (arr[i][0] == "V") {
        gcode.push("V");
      } else if (arr[i][0] == "C") {
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
          gcode.push("G1X" + Math.round(obj.x * 100) / 100 + "Y" + Math.round(obj.y * 100) / 100);
        }
        lastP = [arr[i][5], arr[i][6]];
      } else if (arr[i][0] == "Q") {
        gcode.push("NOT_IMPLEMENTED");
      } else if (arr[i][0] == "A") {
        gcode.push("NOT_IMPLEMENTED");
      } else if (arr[i][0] == "Z") {
        if (lastP[0] != startP[0] && lastP[1] != startP[1]) {
          gcode.push("G1X" + startP[0] + "Y" + startP[1]);
        }
      }
    }
    //console.log(gcode.join("\n").length)
    return gcode.join("\n");
  }
});
