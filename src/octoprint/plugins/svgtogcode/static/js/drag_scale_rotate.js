//    Drag, Scale & Rotate - a snapsvg.io plugin to free transform objects in an svg.
//    Copyright (C) 2015  Teja Philipp <osd@tejaphilipp.de>
//
//    heavily inspired by http://svg.dabbles.info
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
	 *
	 *
	 * @returns {undefined}
	 */
	Element.prototype.transformable = function () {
		var elem = this;
		if (!elem || !elem.paper) // don't handle unplaced elements. this causes double handling.
			return;

		// add invisible fill for better dragging.
		elem.add_fill();
		elem.click(function(){ elem.ftCreateHandles() });
		return elem;


	};

	/**
	 * Adds transparent fill if not present.
	 * This is useful for dragging the element around.
	 *
	 * @returns {path}
	 */
	Element.prototype.add_fill = function(){
		var elem = this;
		var children = elem.selectAll('*');
		if (children.length > 0) {
			for (var i = 0; i < children.length; i++) {
				var child = children[i];
				child.add_fill();
			}
		} else {
			var fill = elem.attr('fill');
			var type = elem.type;
			if(type === 'path' && (fill === 'none' || fill === '')){

				elem.attr({fill: '#ffffff', "fill-opacity": 0});
			}
		}
		return elem;
	};



});


/**
 * Free transform plugin heavily inspired by http://svg.dabbles.info
 */
(function() {

	Snap.plugin( function( Snap, Element, Paper, global ) {

		var ftOption = {
			handleFill: "red",
			handleStrokeDashPreset: [5,5],
			handleStrokeWidth: 2,
			handleLength: 10,
			handleRadius: 16,
			unscale: 1,
			handleStrokeDash: "5,5",
		};

		Element.prototype.ftToggleHandles = function(){
			if(this.data('handlesGroup')){
				this.ftRemoveHandles();
			} else {
				this.ftCreateHandles();
			}
		};

		Element.prototype.ftCreateHandles = function() {
			this.ftInit();
			var freetransEl = this;
			var bb = freetransEl.getBBox();

			var rotateDragger = this.paper.select('#userContent').circle(bb.cx + bb.width/2 + ftOption.handleLength * ftOption.unscale, bb.cy, ftOption.handleRadius * ftOption.unscale ).attr({ fill: ftOption.handleFill });
			var translateDragger = this.paper.select('#userContent').circle(bb.cx, bb.cy, ftOption.handleRadius * ftOption.unscale).attr({ fill: ftOption.handleFill });

			var joinLine = freetransEl.ftDrawJoinLine( rotateDragger, ftOption.handleStrokeWidth * ftOption.unscale);
			var handlesGroup = this.paper.select('#userContent').g( joinLine, rotateDragger, translateDragger );

			freetransEl.data( "handlesGroup", handlesGroup );
			freetransEl.data( "joinLine", joinLine);

			freetransEl.data( "scaleFactor", calcDistance( bb.cx, bb.cy, rotateDragger.attr('cx'), rotateDragger.attr('cy') ) );

			translateDragger.drag(
				elementDragMove.bind( translateDragger, freetransEl ),
				elementDragStart.bind( translateDragger, freetransEl ),
				elementDragEnd.bind( translateDragger, freetransEl )
			);

			freetransEl.unclick();
			freetransEl.data("click", freetransEl.click( function() {  this.ftRemoveHandles() } ) );

			rotateDragger.drag(
				dragHandleRotateMove.bind( rotateDragger, freetransEl ),
				dragHandleRotateStart.bind( rotateDragger, freetransEl  ),
				dragHandleRotateEnd.bind( rotateDragger, freetransEl  )
			);
			freetransEl.ftStoreInitialTransformMatrix();
			freetransEl.ftHighlightBB();
			return this;
		};

		Element.prototype.ftInit = function() {
			this.data("angle", 0);
			this.data("scale", 1);
			this.data("tx", 0);
			this.data("ty", 0);
			this.attr({class:'_freeTransformInProgress'});

			ftOption.unscale = 1 / this.paper.select('#scaleGroup').transform().globalMatrix.a;
			this.data('unscale', ftOption.unscale);
			ftOption.handleStrokeDash = ftOption.handleStrokeDashPreset.map(function(v){ return v*ftOption.unscale; }).join(',');
			return this;
		};

		Element.prototype.ftCleanUp = function() {
			var myClosureEl = this;
			var myData = ["angle", "scale", "scaleFactor", "tx", "ty", "otx", "oty", "bb", "bbT", "initialTransformMatrix", "handlesGroup", "joinLine"];
			myData.forEach( function( el ) { myClosureEl.removeData([el]) });
			return this;
		};

		Element.prototype.ftStoreStartCenter = function() {
			this.data('ocx', this.attr('cx') );
			this.data('ocy', this.attr('cy') );
			return this;
		}

		Element.prototype.ftStoreInitialTransformMatrix = function() {
			this.data('initialTransformMatrix', this.transform().localMatrix );
			return this;
		};

		Element.prototype.ftGetInitialTransformMatrix = function() {
			return this.data('initialTransformMatrix');
		};

		Element.prototype.ftRemoveHandles = function() {
			this.unclick();
			this.removeClass('_freeTransformInProgress');
			this.data( "handlesGroup").remove();
			this.data( "bbT" ) && this.data("bbT").remove();
			this.data( "bb" ) && this.data("bb").remove();
			this.click( function() { this.ftCreateHandles(); } ) ;
			this.ftCleanUp();
			return this;
		};

		Element.prototype.ftDrawJoinLine = function( handle ) {
			var lineAttributes = { stroke: ftOption.handleFill, strokeWidth: ftOption.handleStrokeWidth * ftOption.unscale, strokeDasharray: ftOption.handleStrokeDash };

			var rotateHandle = handle.parent()[1];
			//var dragHandle = handle.parent()[2];
			var thisBB = this.getBBox();

			//var objtps = this.ftTransformedPoint( thisBB.cx, thisBB.cy);

			if( this.data("joinLine")) {
				this.data("joinLine").attr({ x1: thisBB.cx, y1: thisBB.cy, x2: rotateHandle.attr('cx'), y2: rotateHandle.attr('cy') });
			} else {
				return this.paper.line( thisBB.cx, thisBB.cy, handle.attr('cx'), handle.attr('cy') ).attr( lineAttributes );
			};

			return this;
		};

		Element.prototype.ftUpdateTransform = function() {
			//console.log("translate: ", this.data('tx'), this.data('ty'), 'rotate: ', this.data('angle'), 'scale: ', this.data('scale'));
			var tstring = "t" + this.data("tx") + "," + this.data("ty") + this.ftGetInitialTransformMatrix().toTransformString() + "r" + this.data("angle") + 'S' + this.data("scale" );
			this.attr({ transform: tstring });
			this.data("bbT") && this.ftHighlightBB(this.paper.select('#userContent'));
			this.ftReportTransformation();
			return this;
		};

		Element.prototype.ftHighlightBB = function() {
			this.data("bbT") && this.data("bbT").remove();
			this.data("bb") && this.data("bb").remove();

			// transformed bbox
			this.data("bbT", this.paper.rect( rectObjFromBB( this.getBBox(1) ) )
							.attr({ fill: "none", stroke: ftOption.handleFill, strokeWidth: ftOption.handleStrokeWidth, strokeDasharray: ftOption.handleStrokeDashPreset.join(',') })
							.transform( this.transform().local.toString() ) );
			// outer bbox
			this.data("bb", this.paper.select('#userContent').rect( rectObjFromBB( this.getBBox() ) )
							.attr({ fill: "none", stroke: 'gray', strokeWidth: 0.6, strokeDasharray: [2,2] }) );
			return this;
		};

		Element.prototype.ftReportTransformation = function(){
			if(this.data('ftCallbacks') && this.data('ftCallbacks').length > 0){
				for (var idx = 0; idx < this.data('ftCallbacks').length; idx++) {
					var cb = this.data('ftCallbacks')[idx];
					cb(this);
				}
			}
		};
		Element.prototype.ftRegisterCallback = function(callback){
			if(typeof this.data('ftCallbacks') === 'undefined'){
				this.data('ftCallbacks', [callback]);
			} else {
				this.data('ftCallbacks').push(callback);
			}
		};

		Element.prototype.ftDisableRotate = function(){
			this.data('block_rotation', true);
		};
	});

	function rectObjFromBB ( bb ) {
		return { x: bb.x, y: bb.y, width: bb.width, height: bb.height };
	}

	function elementDragStart( mainEl, x, y, ev ) {
		this.parent().selectAll('circle').forEach( function( el, i ) {
				el.ftStoreStartCenter();
		} );
		mainEl.data("otx", mainEl.data("tx") || 0);
		mainEl.data("oty", mainEl.data("ty") || 0);
		mainEl.data('obb', mainEl.getBBox());
		mainEl.data('wa', snap.select('#coordGrid').getBBox());
	};

	function elementDragMove( mainEl, dx, dy, x, y ) {
		var dragHandle = this;
		var unscale = mainEl.data('unscale');
		var bb = mainEl.data('obb');

		var udx = dx*unscale;
		var udy = dy*unscale;

		// check limits
//		udx = Math.max(udx, -bb.x);
//		udx = Math.min(udx, mainEl.data('wa').x2 - bb.x2);
//		udy = Math.max(udy, -bb.y);
//		udy = Math.min(udy, mainEl.data('wa').y2 - bb.y2);

		// update drag handle
		this.parent().selectAll('circle').forEach( function( el, i ) {
			el.attr({ cx: +el.data('ocx') + udx, cy: +el.data('ocy') + udy });
		} );

		// update element
		var tx = mainEl.data("otx") + +udx;
		var ty = mainEl.data("oty") + +udy;
		mainEl.data("tx", tx);
		mainEl.data("ty", ty);
		mainEl.ftUpdateTransform();
		mainEl.ftDrawJoinLine( dragHandle );
	}

	function elementDragEnd( mainEl, dx, dy, x, y ) {
	};

	function dragHandleRotateStart( mainElement ) {
		this.ftStoreStartCenter();
	};

	function dragHandleRotateEnd( mainElement ) {
	};


	function dragHandleRotateMove( mainEl, dx, dy, x, y, event ) {
		var handle = this;
		var mainBB = mainEl.getBBox();
		var unscale = mainEl.data('unscale');
		handle.attr({ cx: +handle.data('ocx') + dx*unscale, cy: +handle.data('ocy') + dy*unscale });

		if(!mainEl.data('block_rotation')){
			var angle = Snap.angle( mainBB.cx, mainBB.cy, handle.attr('cx'), handle.attr('cy') ) - 180;
			if(event.shiftKey){
				angle = Math.round(angle/15) * 15;
			}
			mainEl.data("angle", angle );
		}

		var distance = calcDistance( mainBB.cx, mainBB.cy, handle.attr('cx'), handle.attr('cy') );
		var scale = distance / mainEl.data("scaleFactor");
		if(event.shiftKey){
			scale = Math.round(scale*4) / 4;
		}
		mainEl.data("scale", scale );

		mainEl.ftUpdateTransform();
		mainEl.ftDrawJoinLine( handle );
	};

	function calcDistance(x1,y1,x2,y2) {
		return Math.sqrt( Math.pow( (x1 - x2), 2)  + Math.pow( (y1 - y2), 2)  );
	}

})();





