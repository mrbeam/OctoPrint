$(function(){

	function WorkingAreaViewModel(params) {
		var self = this;

		self.parser = new gcParser();

		self.loginState = params[0];
		self.settings = params[1];
		self.state = params[2];
		self.files = params[3];

		self.log = [];

		self.command = ko.observable(undefined);

		self.availableHeight = ko.observable(undefined);
		self.availableWidth = ko.observable(undefined);
		self.px2mm_factor = 1; // initial value
		self.svgDPI = ko.observable(90); // TODO fetch from settings
		self.workingAreaWidthMM = ko.observable(undefined);
		self.workingAreaHeightMM = ko.observable(undefined);
		self.hwRatio = ko.computed(function(){
			// y/x = 297/216 junior, respectively 594/432 senior
			var w = self.workingAreaWidthMM();
			var h = self.workingAreaHeightMM();
			var ratio = h / w;
			return ratio;
		}, self);

		self.workingAreaDim = ko.computed(function(){
			var maxH = self.availableHeight();
			var maxW = self.availableWidth();
			var hwRatio = self.hwRatio();
			if( hwRatio > 0, maxH > 0, maxW > 0){
				var w = 0;
				var h = 0;
				if( maxH/maxW > hwRatio) {
					w = maxW;
					h = maxW * hwRatio;
				} else {
					w = maxH / hwRatio;
					h = maxH;
				}
				var dim = [w,h];
				return dim;
			}
		});

		self.workingAreaWidthPx = ko.computed(function(){
			var dim = self.workingAreaDim();
			return dim ? dim[0] : 1;
		}, self);

		self.workingAreaHeightPx = ko.computed(function(){
			var dim = self.workingAreaDim();
			return dim ? dim[1] : 1;
		}, self);

		self.px2mm_factor = ko.computed(function(){
			return self.workingAreaWidthMM() / self.workingAreaWidthPx();
		});

		// matrix scales svg units to display_pixels
		self.scaleMatrix = ko.computed(function(){
			var m = new Snap.Matrix();
			var factor = 25.4/self.svgDPI() * 1/self.px2mm_factor();
			if(!isNaN(factor)){
				m.scale(factor);
				return m;
			}
			return m;
		});

		// matrix scales svg units to display_pixels
		self.scaleMatrixMMtoDisplay = ko.computed(function(){
			var m = new Snap.Matrix();
			var factor = self.svgDPI()/25.4; // scale mm to 90dpi pixels
			var yShift = self.workingAreaHeightMM(); // 0,0 origin of the gcode is bottom left. (top left in the svg)
			if(!isNaN(factor)){
				m.scale(factor, -factor).translate(0,-yShift);
				return m;
			}
			return m;
		});

		self.placedDesigns = ko.observableArray([]);
		self.working_area_empty = ko.computed(function(){
			return self.placedDesigns().length === 0;
		});

		self.clear = function(){
			snap.selectAll('#userContent>*').remove();
			snap.selectAll('#placedGcodes>*').remove();
			self.placedDesigns([]);
		};


		self.trigger_resize = function(){
			if(typeof(snap) !== 'undefined') self.abortFreeTransforms();
			self.availableHeight(document.documentElement.clientHeight - $('body>nav').outerHeight()  - $('footer>*').outerHeight() - 39); // magic number
			self.availableWidth($('#workingarea div.span8').innerWidth());
		};

		self.move_laser = function(el){
			self.abortFreeTransforms();
			if(self.state.isOperational() && !self.state.isPrinting()){
				var x = self.px2mm(event.offsetX);
				var y = self.px2mm(event.toElement.ownerSVGElement.offsetHeight - event.offsetY); // hopefully this works across browsers
				x = Math.min(x, self.workingAreaWidthMM());
				y = Math.min(y, self.workingAreaHeightMM());
				$.ajax({
					url: API_BASEURL + "printer/printhead",
					type: "POST",
					dataType: "json",
					contentType: "application/json; charset=UTF8",
					data: JSON.stringify({"command": "position", x:x, y:y})
				});
			}
		};



		self.crosshairX = function(){
			var pos = self.state.currentPos();
			return pos !== undefined ? (self.mm2px(pos.x)  - 15) : -100; // subtract width/2;

		};
		self.crosshairY = function(){
			var h = document.getElementById('area_preview').clientHeight;
			var pos = self.state.currentPos();
			return pos !== undefined ? (h - self.mm2px(pos.y)  - 15) : -100; // subtract height/2;
		};

		self.px2mm = function(val){
			return val * self.px2mm_factor();
		};

		self.mm2px = function(val){
			return val / self.px2mm_factor();
		};

		self.mm2svgUnits = function(val){
			return val * self.svgDPI()/25.4;
		};

		self.isPlaced = function(file){
			if(file === undefined) return false;

			var filePlaced = ko.utils.arrayFirst(this.placedDesigns(), function(d) {
				return d.name === file.name;
			});
			return filePlaced;
		};

		self.placeGcode = function(file){
			var previewId = self.getEntryId(file);

			if(snap.select('#'+previewId)){
				console.error("working_area placeGcode: file already placed.");
				return;
			} else {
				var g = snap.group();
				g.attr({id: previewId});
				snap.select('#placedGcodes').append(g);
				self.placedDesigns.push(file);
			}

			self.loadGcode(file, function(gcode){
				var pathCallback = function(path){
					var points = [];
					var intensity = -1;
					for (var idx = 0; idx < path.length; idx++) {
						var item = path[idx];
						points.push( [ item.x, item.y ] );
						intensity = item.laser;
					}
					if(points.length > 0)
					self.draw_gcode(points, intensity, '#'+previewId);

				};
				var imgCallback = function(x,y,w,h, url){
					self.draw_gcode_img_placeholder(x,y,w,h,url, '#'+previewId)
				};
				self.parser.parse(gcode, /(m0?3)|(m0?5)/i, pathCallback, imgCallback);
			});
		};

		self.loadGcode = function(file, callback){
			var url = file.refs.download;
			var date = file.date;
			$.ajax({
                url: url,
                data: { "ctime": date },
                type: "GET",
                success: function(response, rstatus) {
                    if(rstatus === 'success'){
						if(typeof(callback) === 'function'){
							callback(response);
						}
                    }
                },
                error: function() {
					console.error("working_area.js placeGcode: unable to load ", url);
                }
            });

		};

		self.removeGcode = function(file){
			var previewId = self.getEntryId(file);
			snap.select('#' + previewId).remove();
			self.placedDesigns.remove(file);
		};

		self.placeSVG = function(file) {
			var url = self._getSVGserveUrl(file);
			callback = function (f) {
				var newSvgAttrs = {};
				var root_attrs = f.select('svg').node.attributes;
				var doc_width = null;
				var doc_height = null;
				var doc_viewbox = null;

				// iterate svg tag attributes
				for(var i = 0; i < root_attrs.length; i++){
					var attr = root_attrs[i];

					// get dimensions
					if(attr.name === "width") doc_width = attr.value;
					if(attr.name === "height") doc_height = attr.value;
					if(attr.name === "viewBox") doc_viewbox = attr.value;

					// copy namespaces into group
					if(attr.name.indexOf("xmlns") === 0){
						newSvgAttrs[attr.name] = attr.value;
					}
				}

				// scale matrix
				var mat = self.getDocumentViewBoxMatrix(doc_width, doc_height, doc_viewbox);
				var scaleMatrixStr = new Snap.Matrix(mat[0][0],mat[0][1],mat[1][0],mat[1][1],mat[0][2],mat[1][2]).toTransformString();
				newSvgAttrs['transform'] = scaleMatrixStr;

				var newSvg = snap.group(f.selectAll("svg>*"));
				var hasText = newSvg.selectAll('text,tspan');
				if(hasText !== null && hasText.length > 0){
					self.svg_contains_text_warning(newSvg);
				}

				newSvg.bake(); // remove transforms
				newSvg.selectAll('path').attr({strokeWidth: '0.5'});
				newSvg.attr(newSvgAttrs);
				var id = self.getEntryId(file);
				var previewId = self.generateUniqueId(id); // appends -# if multiple times the same design is placed.
				newSvg.attr({id: previewId});
				snap.select("#userContent").append(newSvg);
				newSvg.transformable();
				newSvg.ftRegisterCallback(self.svgTransformUpdate);

				file.id = id; // list entry id
				file.previewId = previewId;
				file.url = url;
				file.misfit = "";

				self.placedDesigns.push(file);
			};
			self.loadSVG(url, callback);
		};

		self.loadSVG = function(url, callback){
			Snap.load(url, callback);
		};

		self.removeSVG = function(file){
			self.abortFreeTransforms();
			snap.selectAll('#'+file.previewId).remove();
			self.placedDesigns.remove(file);
			// TODO debug why remove always clears all items of this type.
//			self.placedDesigns.remove(function(item){
//				console.log("item", item.previewId );
//				//return false;
//				if(item.previewId === file.previewId){
//					console.log("match", item.previewId );
//					return true;
//				} else return false;
//			});
		};
		self.fitSVG = function(file){
			self.abortFreeTransforms();
			var svg = snap.select('#'+file.previewId);
			var fitMatrix = new Snap.Matrix();
			fitMatrix.scale(svg.data('fitMatrix').scale);
			fitMatrix.translate(svg.data('fitMatrix').dx, svg.data('fitMatrix').dy);
			fitMatrix.add(svg.transform().localMatrix);
			svg.transform(fitMatrix);
			svg.data('fitMatrix', null);
			$('#'+file.id).removeClass('misfit');
			self.svgTransformUpdate(svg);
		};

		self.toggleTransformHandles = function(file){
			var el = snap.select('#'+file.previewId);
			if(el){
				el.ftToggleHandles();
			}
		};

		self.svgTransformUpdate = function(svg){
			var globalScale = self.scaleMatrix().a;
			var transform = svg.transform();
			var bbox = svg.getBBox();
			var tx = self.px2mm(bbox.x * globalScale);
			var ty = self.workingAreaHeightMM() - self.px2mm(bbox.y2 * globalScale);
			var startIdx = transform.local.indexOf('r') + 1;
			var endIdx = transform.local.indexOf(',', startIdx);
			var rot = parseFloat(transform.local.substring(startIdx, endIdx)) || 0;
			var horizontal = self.px2mm((bbox.x2 - bbox.x) * globalScale);
			var vertical = self.px2mm((bbox.y2 - bbox.y) * globalScale);
			var id = svg.attr('id');
			var label_id = id.substr(0, id.indexOf('-'));
			$('#'+label_id+' .translation').text(tx.toFixed(1) + ',' + ty.toFixed(1));
			$('#'+label_id+' .horizontal').text(horizontal.toFixed() + 'mm');
			$('#'+label_id+' .vertical').text(vertical.toFixed() + 'mm');
			$('#'+label_id+' .rotation').text(rot.toFixed(1) + '°');
		};



		self.outsideWorkingArea = function(svg){
			var waBB = snap.select('#coordGrid').getBBox();
			var svgBB = svg.getBBox();

			var tooWide = svgBB.w > waBB.w;
			var tooHigh = svgBB.h > waBB.h;
			var scale = 1;
			if(tooWide || tooHigh){
				scale = Math.min(waBB.w / svgBB.w, waBB.h / svgBB.h) - 0.0001; // scale minimal smaller to avoid rounding errors
			}

			var dx = 0;
			var dy = 0;
			var outside = false;
			if(svgBB.x < waBB.x){
				dx = -svgBB.x + 0.01;
				outside = true;
			} else if(svgBB.x2 > waBB.x2){
				dx = -svgBB.x + 0.01;
				outside = true;
			}
			if(svgBB.y < waBB.y){
				dy = -svgBB.y + 0.01;
				outside = true;
			} else 	if(svgBB.y2 > waBB.y2){
				dy = -svgBB.y2 + waBB.y2 - 0.01;
				outside = true;
			}

			return {
				oversized: tooWide || tooHigh,
				outside: outside,
				scale: scale,
				dx: dx,
				dy: dy
			};
		};

		self.svg_contains_text_warning = function(svg){
            var error = "<p>" + gettext("The svg file contains text elements.<br/>Please convert them to paths.<br/>Otherwise they will be ignored.") + "</p>";
            //error += pnotifyAdditionalInfo("<pre>" + data.jqXHR.responseText + "</pre>");
            new PNotify({
                title: "Text elements found",
                text: error,
                type: "warn",
                hide: false
            });
			svg.selectAll('text,tspan').remove();
		};

		self.svg_misfitting_warning = function(svg, misfitting){
			var outside = gettext("<br/>It has been moved to (0,0). ");
			var oversized = gettext("<br/>It has resized to %d %. ", misfitting.scale);
            var error = "<p>" + gettext("The design was originally not fitting into the working area.")
					+ outside + oversized + gettext("<br/>Please check the result.") + "</p>";
            new PNotify({
                title: "Design moved",
                text: error,
                type: "warn",
                hide: false
            });

		};

		self.placeIMG = function (file) {
			var url = self._getIMGserveUrl(file);
			var img = new Image();
			img.onload = function () {

				var wpx = this.width;
				var hpx = this.height;

				var dimPT = self.getUsefulDimensions(wpx, hpx);
				var wPT = dimPT[0];
				var hPT = dimPT[1];

				var y = self.mm2svgUnits(self.workingAreaHeightMM()) - hPT;
				var newImg = snap.image(url, 0, y, wPT, hPT);
				var id = self.getEntryId(file);
				var previewId = self.generateUniqueId(id); // appends # if multiple times the same design is placed.
				newImg.attr({id: previewId, filter: 'url(#grayscale_filter)', 'data-serveurl': url});
				snap.select("#userContent").append(newImg);
				newImg.transformable();
				//newImg.ftDisableRotate();
				newImg.ftRegisterCallback(self.svgTransformUpdate);
				file.id = id;
				file.previewId = previewId;
				file.url = url;
				file.subtype = "bitmap";
				self.placedDesigns.push(file);
			};
			img.src = url;
		};

		self.removeIMG = function(file){
			self.removeSVG(file);
		};

		self.getUsefulDimensions = function(wpx, hpx){
			var maxWidthMM   = wpx * 0.25; // TODO parametrize
			var maxHeightMM  = hpx * 0.25; // TODO parametrize
			var aspectRatio  = wpx / hpx;
			var destWidthMM  = Math.min(self.workingAreaWidthMM() - 2, maxWidthMM);
			var destHeightMM = Math.min(self.workingAreaHeightMM() - 2, maxHeightMM);
			if ((destWidthMM / aspectRatio) > destHeightMM) {
				destWidthMM = destHeightMM * aspectRatio;
			} else {
				destHeightMM = destWidthMM / aspectRatio;
			}
			var destWidthPT = self.mm2svgUnits(destWidthMM);
			var destHeightPT = self.mm2svgUnits(destHeightMM);
			return [destWidthPT, destHeightPT];
		};

		self.getDocumentDimensionsInPt = function(doc_width, doc_height, doc_viewbox){
			if(doc_width === null){
				// assume defaults if not set
				if(doc_viewbox !== null ){
					var parts = doc_viewbox.split(' ');
					if(parts.length === 4){
						doc_width = parts[2];
					}
				}
				if(doc_width === "100%"){
					doc_width = 744.09; // 210mm @ 90dpi
				}
				if(doc_width === null){
					doc_width = 744.09; // 210mm @ 90dpi
				}
			}
			if(doc_height === null){
				// assume defaults if not set
				if(doc_viewbox !== null ){
					var parts = doc_viewbox.split(' ');
					if(parts.length === 4){
						doc_height = parts[3];
					}
				}
				if(doc_height === "100%"){
					doc_height = 1052.3622047; // 297mm @ 90dpi
				}
				if(doc_height === null){
					doc_height = 1052.3622047; // 297mm @ 90dpi
				}
			}

			var widthPt = self.unittouu(doc_width);
			var heightPt = self.unittouu(doc_height);

			return [widthPt, heightPt];
		};

		self.getDocumentViewBoxMatrix = function(widthStr, heightStr, vbox){
			var dim = self.getDocumentDimensionsInPt(widthStr, heightStr, vbox);
			if(vbox !== null ){
				var widthPx = dim[0];
				var heightPx = dim[1];
				var parts = vbox.split(' ');
				if(parts.length === 4){
					var offsetVBoxX = parseFloat(parts[0]);
					var offsetVBoxY = parseFloat(parts[1]);
					var widthVBox = parseFloat(parts[2]) - parseFloat(parts[0]);
					var heightVBox = parseFloat(parts[3]) - parseFloat(parts[1]);

					var fx = widthPx / widthVBox;
					var fy = heightPx / heightVBox;
					var dx = offsetVBoxX * fx;
					var dy = offsetVBoxY * fy;
					return [[fx,0,0],[0,fy,0], [dx,dy,1]];

				}
			}
			return [[1,0,0],[0,1,0], [0,0,1]];
		};

		//a dictionary of unit to user unit conversion factors
		self.uuconv = {
			'in':90.0,
			'pt':1.25,
			'px':1,
			'mm':3.5433070866,
			'cm':35.433070866,
			'm':3543.3070866,
			'km':3543307.0866,
			'pc':15.0,
			'yd':3240 ,
			'ft':1080
		};

		// Returns userunits given a string representation of units in another system'''
		self.unittouu = function(string){
			var unit_re = new RegExp('(' + Object.keys(self.uuconv).join('|') +')$');

			var unit_factor = 1;
			var u_match = string.match(unit_re);
			if(u_match !== null){
				var unit = string.substring(u_match.index);
				string = string.substring(0,u_match.index);
				if(self.uuconv[unit])
					unit_factor = self.uuconv[unit];
			}

			var p = parseFloat(string);
			if(p)
				return p * unit_factor;
			return 0;
		};

		self._getSVGserveUrl = function(file){
			if (file && file["refs"] && file["refs"]["download"]) {
				var url = file.refs.download.replace("downloads", "serve") +'?'+ Date.now(); // be sure to avoid caching.
				return url;
			}

		};

		self._getIMGserveUrl = function(file){
			return self._getSVGserveUrl(file);
		};

		self.templateFor = function(data) {
			if(data.type === "model" || data.type === "machinecode"){
				var extension = data.name.split('.').pop().toLowerCase();
				if (extension === "svg") {
					return "wa_template_" + data.type + "_svg";
				} else if (_.contains(['jpg', 'jpeg', 'png', 'gif', 'bmp', 'pcx', 'webp'], extension)) {
					return "wa_template_" + data.type + "_img";
				} else {
					return "wa_template_" + data.type;
				}
			} else {
				return "wa_template_dummy";
			}
		};

		self.getEntryId = function(data) {
			return "wa_" + md5(data["origin"] + ":" + data["name"]);
		};


		self.init = function(){
			// init snap.svg
			snap = Snap('#area_preview');
			self.px2mm_factor.subscribe(function(newVal){
				if(!isNaN(newVal))
					self.draw_coord_grid();
			});
		};

		self.draw_coord_grid = function(){
			var grid = snap.select('#coordGrid');
			if(grid.attr('fill') === 'none'){
				var w = self.mm2svgUnits(self.workingAreaWidthMM());
				var h = self.mm2svgUnits(self.workingAreaHeightMM());
				var max_lines = 20;

				var linedistMM = Math.floor(Math.max(self.workingAreaWidthMM(), self.workingAreaHeightMM()) / (max_lines * 10))*10;
				var yPatternOffset = self.mm2svgUnits(self.workingAreaHeightMM() % linedistMM);
				var linedist = self.mm2svgUnits(linedistMM);

				var marker = snap.circle(linedist/2, linedist/2, 1).attr({
					fill: "#000000",
					stroke: "none",
					strokeWidth: 1
				});

				// dot pattern
				var p = marker.pattern(0, 0, linedist, linedist);
				p.attr({
					x: linedist/2,
					y: linedist/2 + yPatternOffset
				});

				grid.attr({
					width: w,
					height: h,
					fill: p
				});
			}
		};

		self.generateUniqueId = function(idBase){
			var suffix = 0;
			var id = idBase + "-" + suffix;
			while(snap.select('#'+id) !== null){
				suffix += 1;
				id = idBase + "-" + suffix;
			}
			return id;
		};

		self.abortFreeTransforms = function(){
			var tip = snap.selectAll('._freeTransformInProgress');
			for (var i = 0; i < tip.length; i++) {
				var el = tip[i];
				el.ftRemoveHandles();
			}
			self.check_sizes_and_placements();
		};

		self.getCompositionSVG = function(fillAreas, callback){
			self.abortFreeTransforms();
			var wMM = self.workingAreaWidthMM();
			var hMM = self.workingAreaHeightMM();
			var wPT = wMM * 90 / 25.4;
			var hPT = hMM * 90 / 25.4;
			var compSvg = Snap(wPT, hPT);
			compSvg.attr('id', 'compSvg');

			var userContent = snap.select("#userContent").clone();
			compSvg.append(userContent);

			self.renderInfill(compSvg, fillAreas, wMM, hMM, 10, function(svgWithRenderedInfill){
				callback( self._wrapInSvgAndScale(svgWithRenderedInfill));
				$('#compSvg').remove();
			});
		};

		self._wrapInSvgAndScale = function(content){
			var svgStr = content.innerSVG();
			if(svgStr !== ''){
				var dpiFactor = self.svgDPI()/25.4; // convert mm to pix 90dpi for inkscape, 72 for illustrator
				var w = dpiFactor * self.workingAreaWidthMM();
				var h = dpiFactor * self.workingAreaHeightMM();

				// TODO: look for better solution to solve this Firefox bug problem
				svgStr = svgStr.replace("(\\\"","(");
				svgStr = svgStr.replace("\\\")",")");

				var svg = '<svg height="'+ h +'" version="1.1" width="'+ w +'" xmlns="http://www.w3.org/2000/svg"><defs/>'+ svgStr +'</svg>';
				return svg;
			} else {
				return;
			}
		};

		self.getPlacedSvgs = function() {
			var svgFiles = [];
			ko.utils.arrayForEach(self.placedDesigns(), function(design) {
				if(design.type === 'model'){
				var extension = design.name.split('.').pop().toLowerCase();
					if (extension === "svg") {
						svgFiles.push(design);
					}
				}
			});
			return svgFiles;
		};

		self.getPlacedImages = function(){
			return snap.selectAll("#userContent image");
		};

		self.getPlacedGcodes = ko.computed(function() {
			var gcodeFiles = [];
			ko.utils.arrayForEach(self.placedDesigns(), function(design) {
				if(design.type === 'machinecode') gcodeFiles.push(design);
			});
			return gcodeFiles;
		}, self);

		self.hasFilledVectors = function(){
			var el = snap.selectAll('#userContent *');
			for (var i = 0; i < el.length; i++) {
				var e = el[i];
				var fill = e.attr('fill');
				var op = e.attr('fill-opacity');
				if(fill !== 'none' && op > 0){
					return true;
				}

			}
			return false;
		};

		self.draw_gcode = function(points, intensity, target){
			var stroke_color = intensity === 0 ? '#BBBBBB' : '#FF0000';
			var d = 'M'+points.join(' ');
			var p = snap.path(d).attr({
				fill: "none",
				stroke: stroke_color,
				strokeWidth: 1
			});
			snap.select(target).append(p);
		};

		self.draw_gcode_img_placeholder = function(x,y,w,h,url, target){
			if(url !== ""){
				var p = snap.image(url,x,y,w,h).attr({
					transform: 'matrix(1,0,0,-1,0,'+ String(h) +')',
					filter: 'url(#gcimage_preview)'
				});

			}
			snap.select(target).append(p);
		};

		self.clear_gcode = function(){
			snap.select('#gCodePreview').clear();
		};

		self.onStartup = function(){
			self.state.workingArea = self;
			self.files.workingArea = self;

			// check this on tab change as before the bounding boxes are sized 0.
			$('#wa_tab_btn').on('shown.bs.tab', function (e) {
				self.trigger_resize();
				self.check_sizes_and_placements();
			});
			$(window).resize(function(){
				self.trigger_resize();
			});
			self.trigger_resize(); // initialize
			self.init();
		};

		self.check_sizes_and_placements = function(){
			ko.utils.arrayForEach(self.placedDesigns(), function(design) {
				if(design.type === 'model'){
					var svg = snap.select('#' + design.previewId);
					var misfitting = self.outsideWorkingArea(svg);
					if(misfitting.oversized || misfitting.outside){
						svg.data('fitMatrix', misfitting);
						$('#'+design.id).addClass('misfit');

					} else {
						$('#'+design.id).removeClass('misfit');
						svg.data('fitMatrix', null);
					}
				}
			});
		};

		self._embedAllImages = function(svg, callback){

			var allImages = svg.selectAll('image');
			var linkedImages = allImages.items.filter(function(i){
				if(i.attr('xlink:href') != null) {
					return !i.attr('xlink:href').startsWith('data:');
				} else if(i.attr('href') != null) {
					return !i.attr('href').startsWith('data:');
				}});
			if(linkedImages.length > 0){
				var callbackCounter = linkedImages.length;
				for (var i = 0; i < linkedImages.length; i++) {
					var img = linkedImages[i];
					img.embedImage(function(){
						callbackCounter--;
						if(callbackCounter === 0 && typeof callback === 'function'){
							callback();
						}
					});
				}
			} else {
				// callback if nothing to embed
				if(typeof callback === 'function'){
					callback();
				}
			}
		}

		// render the infill and inject it as an image into the svg
		self.renderInfill = function (svg, fillAreas, wMM, hMM, pxPerMM, callback) {
			var wPT = wMM * 90 / 25.4;
			var hPT = hMM * 90 / 25.4;
			var tmpSvg = Snap(wPT, hPT).attr('id', 'tmpSvg');
			// get only filled items and embed the images
			var userContent = svg.clone();
			tmpSvg.append(userContent);
			self._embedAllImages(tmpSvg, function(){
				var fillings = userContent.removeUnfilled(fillAreas);
				for (var i = 0; i < fillings.length; i++) {
					var item = fillings[i];

					if (item.type === 'image') {
						// remove filter effects on images for proper rendering
						var style = item.attr('style');
						if (style !== null) {
							var strippedFilters = style.replace(/filter.+?;/, '');
							item.attr('style', strippedFilters);
						}
					} else {
						// remove stroke from other elements
						//item.attr('fill', '#ff0000');
						item.attr('stroke', 'none');
					}
				}

				var cb = function(result) {
					if(fillings.length > 0){
						// replace all images with the fill rendering
						svg.selectAll('image').remove();
						var waBB = snap.select('#coordGrid').getBBox();
						var fillImage = snap.image(result, 0, 0, waBB.w, waBB.h);
						fillImage.attr('id', 'fillRendering');
						svg.append(fillImage);
					}
					if (typeof callback === 'function') {
						callback(svg);
					}
					self._cleanup_render_mess();
				};

				tmpSvg.renderPNG(wMM, hMM, pxPerMM, cb);
			});
		};

		self._cleanup_render_mess = function(){
			$('#tmpSvg').remove();
		};

		self.onBeforeBinding = function(){
			self.files.workingArea = self;
		};
	}


    // view model class, parameters for constructor, container to bind to
    ADDITIONAL_VIEWMODELS.push([WorkingAreaViewModel,
		["loginStateViewModel", "settingsViewModel", "printerStateViewModel",  "gcodeFilesViewModel"],
		[document.getElementById("area_preview"), document.getElementById("working_area_files")]]);

});
