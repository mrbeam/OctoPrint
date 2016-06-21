//    extrude.js - a snapsvg.io plugin to generate extrusion walls of svg paths.
//    Copyright (C) 2016  Teja Philipp <osd@tejaphilipp.de>
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
	 * @param {elem} elem start point
	 * 
	 * @returns {path}
	 */

	Element.prototype.extrude = function(height, perforation_curvature_threshold){
		var subdiv_length = 1;
		var material_thickness = 1;
		var perforation_curvature_threshold = perforation_curvature_threshold || 5;
		var elem = this;
		var walls = [];
		var children = elem.children();

		if (children.length > 0) {
			var goRecursive = (elem.type !== "defs" && // ignore these tags
				elem.type !== "clipPath" &&
				elem.type !== "metadata" &&
				elem.type !== "rdf:rdf" &&
				elem.type !== "cc:work" &&
				elem.type !== "sodipodi:namedview");
		
			if(goRecursive) {
				for (var i = 0; i < children.length; i++) {
					var child = children[i];
					var more = child.extrude(height);
					walls.push.apply(walls, more);
				}
			}
		} else {
			var e;
			if (elem.type !== "path"){
				console.log('converting element to path', elem.type);
				e = elem.toPath();
			} else {
				e = elem;
			}
			
			var dOriginal = e.attr('d');
			var parts = Snap.path.toRelative(dOriginal).toString();
			var segments = parts.split('m');
			
			console.log('d',dOriginal);
			for (var i = 0; i < segments.length; i++) {
				if(segments[i].trim() !== ''){
					var seg = 'M' + segments[i];
					var l = Snap.path.getTotalLength(seg);
					console.log("segment", seg, l);
					walls.push(createWrappingPath2(seg), height);
				}
			}

			//walls.push( createWrappingPath(segments, height));
		}
		return walls;
	};

	function createWrappingPath2(segment, height){
		var l = Snap.path.getTotalLength(segment);
		console.log(segment, l);
		
		// create "rectangle" 
		var rect_d = "m0,0 l"+l+',0' // top
		+ ' l0,'+height // right
		+ ' l-'+l+',0' // bottom
		+ ' l0,-'+height; // left

		var perforations = getPerforations(segment, 5);
		var perf_d = [];
		for (var i = 0; i < perforations.length; i++) {
			var perf = perforations[i];
			var density = 0.8 * perf.curvature/90;
			perf_d.push(createVerticalPerforation(perf.loc,height,density, height/10));
			
		}
		return rect_d+perf_d.join(',');

	}
	
	function getPerforations(d, perf_curvature_threshold){
		var stepSize = 1;
		var perfs = [];
		var e = snap.path(d)
		var l = e.getTotalLength();
		var i = 0;
		var last_alpha = -999;
		var curvature_sum = 0;
		while(i<l){
			var p = e.getPointAtLength(i);
			p.curvature = last_alpha - p.alpha;
			p.loc = i;
			curvature_sum += p.curvature;
			last_alpha = p.alpha;
			if(Math.abs(curvature_sum) > perf_curvature_threshold){
				perfs.push({loc:i, curve:p.curvature});
				curvature_sum = 0;
			}
			i+=stepSize;
		}
		return perfs;
	}

	function createWrappingPath(divs, height){
		var upper_d = "M0,0";
		var lower_d = "M0,"+height;
		var verticals = ['M0,0l0,'+height];
		for (var i = 0; i < divs.length; i++) {
			var d = divs[i];
			var x = d.loc.toFixed(2);
			upper_d += 'L'+x+',0';
			lower_d += 'L'+x+','+height;
			var density = 0.8* d.curvature/180;
			verticals.push(createVerticalPerforation(x,height, density, height/15));	
		}
		return upper_d+lower_d+verticals.join();
	}
	
	function createVerticalPerforation(x, height, density, perfLength){
		var cut = (perfLength * (density)).toFixed(2);
		var gap = (perfLength - cut).toFixed(2);
		var d = 'M'+x+',0';
		var y = 0;
		while(y < height){
			d += 'l0,'+cut+'m0,'+gap;
			y += perfLength;
		}
		return d;
	}
});









