//    Matrix Oven - a snapsvg.io plugin to apply & remove transformations from svg files.
//    Copyright (C) 2015  Teja Philipp <osd@tejaphilipp.de>
//    
//    based on work by https://gist.github.com/timo22345/9413158 
//    and https://github.com/duopixel/Method-Draw/blob/master/editor/src/svgcanvas.js
//
//    This program is free software: you can redistribute it and/or modify
//    it under the terms of the GNU Affero General Public License as
//    published by the Free Software Foundation, either version 3 of the
//    License, or (at your option) any later version.
//
//    This program is distributed in the hope that it will be useful,
//    but WITHOUT ANY WARRANTY; without even the implied warranty of
//    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
//    GNU Affero General Public License for more details.
//
//    You should have received a copy of the GNU Affero General Public License
//    along with this program.  If not, see <http://www.gnu.org/licenses/>.



Snap.plugin(function (Snap, Element, Paper, global) {
	
	/**
	 * bakes transformations of the element and all sub-elements into coordinates
	 * 
	 * @param {boolean} toCubics : use only cubic path segments
	 * @param {integer} dec : number of digits after decimal separator. defaults to 5
	 * @returns {undefined}
	 */
	Element.prototype.bake = function (toCubics, dec) {
		var elem = this;
		if (!elem || !elem.paper) // don't handle unplaced elements. this causes double handling.
			return;

		if (typeof (toCubics) === 'undefined')
			toCubics = false;
		if (typeof (dec) === 'undefined')
			dec = 5;
		//var children = elem.selectAll('*')
		var children = elem.children();
		if (children.length > 0) {
			for (var i = 0; i < children.length; i++) {
				var child = children[i];
				child.bake(toCubics, dec);
			}
			elem.attr({transform: ''});
			return;
		}
		if (elem.type !== "circle" &&
			elem.type !== "rect" &&
			elem.type !== "ellipse" &&
			elem.type !== "line" &&
			elem.type !== "polygon" &&
			elem.type !== "polyline" &&
			elem.type !== "image" &&
			elem.type !== "path"){
			
//			if(elem.type !== 'g' && elem.type !== 'desc' && elem.type !== 'defs')
//				console.log('skipping unsupported element ', elem.type);
			return;
		}

		if (elem.type == 'image'){
			// TODO ... 
			var x = parseFloat(elem.attr('x')),
				y = parseFloat(elem.attr('y')),
				w = parseFloat(elem.attr('width')),
				h = parseFloat(elem.attr('height'));

			// Validity checks from http://www.w3.org/TR/SVG/shapes.html#RectElement:
			// If 'x' and 'y' are not specified, then set both to 0. // CorelDraw is creating that sometimes
			if (!isFinite(x)) {
				console.log('No attribute "x" in image tag. Assuming 0.')
				x = 0;
			}
			if (!isFinite(y)) {
				console.log('No attribute "y" in image tag. Assuming 0.')
				y = 0;
			}
			var transform = elem.transform();
			var matrix = transform['totalMatrix'];
			var transformedX = matrix.x(x, y);
			var transformedY = matrix.y(x, y);
			var transformedW = matrix.x(x+w, y+h) - transformedX;
			var transformedH = matrix.y(x+w, y+h) - transformedY;
			
			elem.attr({x: transformedX, y: transformedY, width: transformedW, height: transformedH});
			return;
		}

		//if(elem.type !== 'path') console.log("bake: converting " + elem.type + " to path");
		var path_elem = elem.convertToPath();

		if (!path_elem || path_elem.attr('d') === '' || path_elem.attr('d') === null)
			path_elem.attr('d', 'M 0 0');

		// Rounding coordinates to dec decimals
		if (dec || dec === 0) {
			dec = Math.min(Math.max(0,Math.floor(dec)), 15);
		} else {
			dec = false;
		}
		
		function r(num) {
			if (dec !== false) {
				return Math.round(num * Math.pow(10, dec)) / Math.pow(10, dec);
			} else {
				return num;
			}
		}

		var arr;
		var d = path_elem.attr('d');
		d = (d || "").trim();
		var arr_orig;
		arr = Snap.parsePathString(d); 
		if (!toCubics) {  
			arr_orig = arr;
			arr = Snap.path.toAbsolute(arr);
		} else {
			arr = Snap.path.toCubic(arr); // implies absolute coordinates
			arr_orig = arr;
		}

		// Get the transformation matrix between SVG root element and current element
		var transform = path_elem.transform();
		var matrix = transform['totalMatrix'];

		// apply the matrix transformation on the path segments
		var j; 
		var m = arr.length;
		var letter = '';
		var letter_orig = '';
		var x = 0;
		var y = 0;
		var new_segments = [];
		var pt = {x: 0, y: 0};
		var pt_baked = {}; 
		var subpath_start = {}; 
		var prevX = 0;
		var prevY = 0;
		subpath_start.x = null;
		subpath_start.y = null;
		for (var i=0; i < m; i++) {
			letter = arr[i][0].toUpperCase();
			letter_orig = arr_orig[i][0];
			new_segments[i] = [];
			new_segments[i][0] = arr[i][0];

			if (letter === 'A') {
				x = arr[i][6];
				y = arr[i][7];

				pt.x = arr[i][6];
				pt.y = arr[i][7];
				new_segments[i] = _arc_transform(arr[i][1], arr[i][2], arr[i][3], arr[i][4], arr[i][5], pt, matrix);
				
			} else if (letter !== 'Z') {
				// parse other segs than Z and A
				for (j = 1; j < arr[i].length; j = j + 2) {
					if (letter === 'V') {
						y = arr[i][j];
					} else if (letter === 'H') {
						x = arr[i][j];
					} else {
						x = arr[i][j];
						y = arr[i][j + 1];
					}
					pt.x = x;
					pt.y = y;
					pt_baked.x = matrix.x(pt.x, pt.y);
					pt_baked.y = matrix.y(pt.x, pt.y);

					if (letter === 'V' || letter === 'H') {
						new_segments[i][0] = 'L';
						new_segments[i][j] = pt_baked.x;
						new_segments[i][j + 1] = pt_baked.y;
					} else {
						new_segments[i][j] = pt_baked.x;
						new_segments[i][j + 1] = pt_baked.y;
					}
				}
			}
			if ((letter !== 'Z' && subpath_start.x === null) || letter === 'M') {
				subpath_start.x = x;
				subpath_start.y = y;
			}
			if (letter === 'Z') {
				x = subpath_start.x;
				y = subpath_start.y;
			}
		}
		
		// Convert all that was relative back to relative
		// This could be combined to above, but to make code more readable
		// this is made separately.
		var prevXtmp = 0;
		var prevYtmp = 0;
		subpath_start.x = '';
		for (i = 0; i < new_segments.length; i++) {
			letter_orig = arr_orig[i][0];
			if (letter_orig === 'A' || letter_orig === 'M' || letter_orig === 'L' || letter_orig === 'C' || letter_orig === 'S' || letter_orig === 'Q' || letter_orig === 'T' || letter_orig === 'H' || letter_orig === 'V') {
				var len = new_segments[i].length;
				var lentmp = len;
				if (letter_orig === 'A') {
					// rounding arc parameters
					// only x,y are rounded,
					// other parameters are left as they are
					// because they are more sensitive to rounding
					new_segments[i][6] = r(new_segments[i][6]);
					new_segments[i][7] = r(new_segments[i][7]);
				} else {
					lentmp--;
					while (--lentmp){
						new_segments[i][lentmp] = r(new_segments[i][lentmp]);
					}
				}
				prevX = new_segments[i][len - 2];
				prevY = new_segments[i][len - 1];
			} else {
				if (letter_orig === 'a') {
					// same rounding treatment as above for arcs
					prevXtmp = new_segments[i][6];
					prevYtmp = new_segments[i][7];
					new_segments[i][0] = letter_orig;
					new_segments[i][6] = r(new_segments[i][6] - prevX);
					new_segments[i][7] = r(new_segments[i][7] - prevY);
					prevX = prevXtmp;
					prevY = prevYtmp;
				} else if (letter_orig === 'm' || letter_orig === 'l' || letter_orig === 'c' || letter_orig === 's' || letter_orig === 'q' || letter_orig === 't' || letter_orig === 'h' || letter_orig === 'v') {
					var len = new_segments[i].length;
					prevXtmp = new_segments[i][len - 2];
					prevYtmp = new_segments[i][len - 1];
					for (j = 1; j < len; j = j + 2) {
						if (letter_orig === 'h' || letter_orig === 'v') {
							new_segments[i][0] = 'l';
						} else {
							new_segments[i][0] = letter_orig;
						}
						new_segments[i][j] = r(new_segments[i][j] - prevX);
						new_segments[i][j + 1] = r(new_segments[i][j + 1] - prevY);
					}
					prevX = prevXtmp;
					prevY = prevYtmp;
				}
			}
			if ((letter_orig.toLowerCase() !== 'z' && subpath_start.x === '') || letter_orig.toLowerCase() === 'm') {
				subpath_start.x = prevX;
				subpath_start.y = prevY;
			}
			if (letter_orig.toLowerCase() === 'z') {
				prevX = subpath_start.x;
				prevY = subpath_start.y;
			}
		}

		var d_str = _convertToString(new_segments);
		path_elem.attr({d: d_str});
		path_elem.attr({transform: ''});
		//console.log("baked matrix ", matrix, " of ", path_elem.attr('id'));

	};
	
	/**
	 * Helper to apply matrix transformations to arcs.
	 * From flatten.js (https://gist.github.com/timo22345/9413158), modified a bit.
	 *
	 * @param {type} a_rh : r1 of the ellipsis in degree
	 * @param {type} a_rv : r2 of the ellipsis in degree
	 * @param {type} a_offsetrot : x-axis rotation in degree
	 * @param {type} large_arc_flag : 0 or 1 
	 * @param {int} sweep_flag : 0 or 1
	 * @param {object} endpoint with properties x and y
	 * @param {type} matrix : transformation matrix
	 * @returns {Array} : representing the transformed path segment
	 */
	function _arc_transform(a_rh, a_rv, a_offsetrot, large_arc_flag, sweep_flag, endpoint, matrix) {
		function NEARZERO(B) {
			return Math.abs(B) < 0.0000000000000001;
		}


		var m = []; // matrix representation of transformed ellipse
		var A; var B; var C; // ellipse implicit equation:
		var ac; var A2; var C2; // helpers for angle and halfaxis-extraction.
		var rh = a_rh;
		var rv = a_rv;

		a_offsetrot = a_offsetrot * (Math.PI / 180); // deg->rad
		var rot = a_offsetrot;

		// sin/cos helper (the former offset rotation)
		var s = Math.sin(rot);
		var c = Math.cos(rot);

		// build ellipse representation matrix (unit circle transformation).
		// the 2x2 matrix multiplication with the upper 2x2 of a_mat is inlined.
		m[0] = matrix.a * +rh * c + matrix.c * rh * s;
		m[1] = matrix.b * +rh * c + matrix.d * rh * s;
		m[2] = matrix.a * -rv * s + matrix.c * rv * c;
		m[3] = matrix.b * -rv * s + matrix.d * rv * c;

		// to implict equation (centered)
		A = (m[0] * m[0]) + (m[2] * m[2]);
		C = (m[1] * m[1]) + (m[3] * m[3]);
		B = (m[0] * m[1] + m[2] * m[3]) * 2.0;

		// precalculate distance A to C
		ac = A - C;

		// convert implicit equation to angle and halfaxis:
		// disabled intentionally
		if (false && NEARZERO(B)) { // there is a bug in this optimization: does not work for path below 
			a_offsetrot = 0;  
//			 d="M0,350 l 50,-25 
//           a25,25 -30 0,1 50,-25 l 50,-25 
//           a25,50 -30 0,1 50,-25 l 50,-25 
//           a25,75 -30 0,1 50,-25 l 50,-25 
//           a25,100 -30 0,1 50,-25 l 50,-25"
//			with matrix transform="scale(0.5,2.0)"
			A2 = A;
			C2 = C;
		} else {
			if (NEARZERO(ac)) {
				A2 = A + B * 0.5;
				C2 = A - B * 0.5;
				a_offsetrot = Math.PI / 4.0;
			} else {
				// Precalculate radical:
				var K = 1 + B * B / (ac * ac);

				// Clamp (precision issues might need this.. not likely, but better save than sorry)
				K = (K < 0) ? 0 : Math.sqrt(K);
				
				A2 = 0.5 * (A + C + K * ac);
				C2 = 0.5 * (A + C - K * ac);
				a_offsetrot = 0.5 * Math.atan2(B, ac);
			}
		}

		// This can get slightly below zero due to rounding issues.
		// it's save to clamp to zero in this case (this yields a zero length halfaxis)
		A2 = (A2 < 0) ? 0 : Math.sqrt(A2);
		C2 = (C2 < 0) ? 0 : Math.sqrt(C2);

		// now A2 and C2 are half-axis:
		if (ac <= 0){
			a_rv = A2;
			a_rh = C2;
		} else {
			a_rv = C2;
			a_rh = A2;
		}

		// If the transformation matrix contain a mirror-component 
		// winding order of the ellise needs to be changed.
		if ((matrix.a * matrix.d) - (matrix.b * matrix.c) < 0){
			sweep_flag = !sweep_flag ? 1 : 0;
		}

		// Finally, transform arc endpoint. This takes care about the
		// translational part which we ignored at the whole math-showdown above.
		var baked_x = matrix.x(endpoint.x, endpoint.y);
		var baked_y = matrix.y(endpoint.x, endpoint.y);

		// Radians back to degrees
		a_offsetrot = a_offsetrot * 180 / Math.PI;

		var r = ['A', a_rh, a_rv, a_offsetrot, large_arc_flag, sweep_flag, baked_x, baked_y];
		return r;
	}

	// just a helper
	var _p2s = /,?([achlmqrstvxz]),?/gi;
	var _convertToString = function (arr) {
		return arr.join(',').replace(_p2s, '$1');
	};
	
	/**
	 * Replaces an element with a path of same shape.
	 * Supports rect, ellipse, circle, line, polyline, polygon and of course path
	 * The element will be replaced by the path with same id. 
	 * 
	 * @returns {path}
	 */
	Element.prototype.convertToPath = function(){
		var old_element = this;
		var path = old_element.toPath();
		old_element.before(path);
		old_element.remove(); 
		return path;
	};

	/**
	 * Creates a path in the same shape as the origin element
	 * Supports rect, ellipse, circle, line, polyline, polygon and of course path
	 * 
	 * based on 
	 * https://github.com/duopixel/Method-Draw/blob/master/editor/src/svgcanvas.js
	 * Modifications: Timo (https://github.com/timo22345)
	 * 
	 * @returns {path} path element
	 */
	Element.prototype.toPath = function () {
		var old_element = this;

		// Create new path element
		var pathAttr = {};

		// All attributes that path element can have
		var attrs = ['requiredFeatures', 'requiredExtensions', 'systemLanguage', 'id', 'xml:base', 'xml:lang', 'xml:space', 'onfocusin', 'onfocusout', 'onactivate', 'onclick', 'onmousedown', 'onmouseup', 'onmouseover', 'onmousemove', 'onmouseout', 'onload', 'alignment-baseline', 'baseline-shift', 'clip', 'clip-path', 'clip-rule', 'color', 'color-interpolation', 'color-interpolation-filters', 'color-profile', 'color-rendering', 'cursor', 'direction', 'display', 'dominant-baseline', 'enable-background', 'fill', 'fill-opacity', 'fill-rule', 'filter', 'flood-color', 'flood-opacity', 'font-family', 'font-size', 'font-size-adjust', 'font-stretch', 'font-style', 'font-variant', 'font-weight', 'glyph-orientation-horizontal', 'glyph-orientation-vertical', 'image-rendering', 'kerning', 'letter-spacing', 'lighting-color', 'marker-end', 'marker-mid', 'marker-start', 'mask', 'opacity', 'overflow', 'pointer-events', 'shape-rendering', 'stop-color', 'stop-opacity', 'stroke', 'stroke-dasharray', 'stroke-dashoffset', 'stroke-linecap', 'stroke-linejoin', 'stroke-miterlimit', 'stroke-opacity', 'stroke-width', 'text-anchor', 'text-decoration', 'text-rendering', 'unicode-bidi', 'visibility', 'word-spacing', 'writing-mode', 'class', 'style', 'externalResourcesRequired', 'transform', 'd', 'pathLength'];

		// Copy attributes of old_element to path
		for(var attrIdx in attrs){
			var attrName = attrs[attrIdx];
			var attrValue;
			if(attrName === 'transform') {
				attrValue = old_element.transform()['localMatrix'];
			} else {
				attrValue = old_element.attr(attrName);
			}
			if (attrValue) {
				pathAttr[attrName] = attrValue;
			}
		}

		var d = '';

		var validRadius = function (val) {
			return (isFinite(val) && (val >= 0));
		};
		
		var validCoordinate = function (val) {
			return (isFinite(val));
		};

		// Possibly the cubed root of 6, but 1.81 works best
		var num = 1.81;
		var tag = old_element.type;
		switch (tag) {
			case 'ellipse':
			case 'circle':
				var rx = +parseFloat(old_element.attr('rx')),
						ry = +parseFloat(old_element.attr('ry')),
						cx = +parseFloat(old_element.attr('cx')),
						cy = +old_element.attr('cy');
				if (tag === 'circle') {
					rx = ry = +old_element.attr('r');
				}
				
				// If 'x' and 'y' are not specified, then set both to 0. // CorelDraw is creating that sometimes
				if (!validCoordinate(cx))
					cx = 0;
				if (!validCoordinate(cy))
					cy = 0;
				
				d += _convertToString([
					['M', (cx - rx), (cy)],
					['C', (cx - rx), (cy - ry / num), (cx - rx / num), (cy - ry), (cx), (cy - ry)],
					['C', (cx + rx / num), (cy - ry), (cx + rx), (cy - ry / num), (cx + rx), (cy)],
					['C', (cx + rx), (cy + ry / num), (cx + rx / num), (cy + ry), (cx), (cy + ry)],
					['C', (cx - rx / num), (cy + ry), (cx - rx), (cy + ry / num), (cx - rx), (cy)],
					['Z']
				]);
				break;
			case 'path':
				d = old_element.attr('d');
				break;
			case 'line':
				var x1 = parseFloat(old_element.attr('x1')),
						y1 = parseFloat(old_element.attr('y1')),
						x2 = parseFloat(old_element.attr('x2')),
						y2 = old_element.attr('y2');
				d = 'M' + x1 + ',' + y1 + 'L' + x2 + ',' + y2;
				break;
			case 'polyline':
				d = 'M' + old_element.attr('points');
				break;
			case 'polygon':
				d = 'M' + old_element.attr('points') + 'Z';
				break;
			case 'rect':
				// TODO ... 
				var rx = parseFloat(old_element.attr('rx')),
					ry = parseFloat(old_element.attr('ry')),
					x = parseFloat(old_element.attr('x')),
					y = parseFloat(old_element.attr('y')),
					w = parseFloat(old_element.attr('width')),
					h = parseFloat(old_element.attr('height'));

				// Validity checks from http://www.w3.org/TR/SVG/shapes.html#RectElement:
				// If 'x' and 'y' are not specified, then set both to 0. // CorelDraw is creating that sometimes
				if (!validCoordinate(x))
					x = 0;
				if (!validCoordinate(y))
					y = 0;
				// If neither ‘rx’ nor ‘ry’ are properly specified, then set both rx and ry to 0. (This will result in square corners.)
				if (!validRadius(rx) && !validRadius(ry)) {
					rx = ry = 0;
				// Otherwise, if a properly specified value is provided for ‘rx’, but not for ‘ry’, then set both rx and ry to the value of ‘rx’.
				} else if (validRadius(rx) && !validRadius(ry)) {
					ry = rx;
				// Otherwise, if a properly specified value is provided for ‘ry’, but not for ‘rx’, then set both rx and ry to the value of ‘ry’.
				} else if (validRadius(ry) && !validRadius(rx)) {
					rx = ry;
				} else { // cap values for rx/ry to half of w/h
					rx = Math.min(rx, w/2);
					ry = Math.min(ry, h/2);
				}

				if (!rx && !ry) {
					d += _convertToString([
						['M', x, y],
						['L', x + w, y],
						['L', x + w, y + h],
						['L', x, y + h],
						['L', x, y],
						['Z']
					]);
				} else {
					var num = 2.19;
					if (!ry){
						ry = rx;
					}
					d += _convertToString([
						['M', x, y + ry],
						['C', x, y + ry / num, x + rx / num, y, x + rx, y],
						['L', x + w - rx, y],
						['C', x + w - rx / num, y, x + w, y + ry / num, x + w, y + ry],
						['L', x + w, y + h - ry],
						['C', x + w, y + h - ry / num, x + w - rx / num, y + h, x + w - rx, y + h],
						['L', x + rx, y + h],
						['C', x + rx / num, y + h, x, y + h - ry / num, x, y + h - ry],
						['L', x, y + ry],
						['Z']
					]);
				}
				break;
			default:
				break;
		}

		if (d){
			pathAttr.d = d;
		}
		var path = old_element.paper.path(pathAttr);
		return path;
	};




});

