/* ===========================================================
 * bootstrap-modal.js v2.2.5
 * ===========================================================
 * Copyright 2012 Jordan Schroter
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 * ========================================================== */


!function ($) {

	"use strict"; // jshint ;_;

	/* MODAL CLASS DEFINITION
	* ====================== */

	var Modal = function (element, options) {
		this.init(element, options);
	};

	Modal.prototype = {

		constructor: Modal,

		init: function (element, options) {
			var that = this;

			this.options = options;

			this.$element = $(element)
				.delegate('[data-dismiss="modal"]', 'click.dismiss.modal', $.proxy(this.hide, this));

			this.options.remote && this.$element.find('.modal-body').load(this.options.remote, function () {
				var e = $.Event('loaded');
				that.$element.trigger(e);
			});

			var manager = typeof this.options.manager === 'function' ?
				this.options.manager.call(this) : this.options.manager;

			manager = manager.appendModal ?
				manager : $(manager).modalmanager().data('modalmanager');

			this.manager = manager;
			this.$parent = this.$element.parent();
			this._transitions = {};
			manager.appendModal(this);
		},

		toggle: function () {
			return this[!this.isShown ? 'show' : 'hide']();
		},

		show: function () {
			var e = $.Event('show');

			if (this.isHiding) {
				this._showAfterHide = true;
				return;
			}
			if (this._destroyed || this.isShown) return;

			this.$element.trigger(e);

			if (e.isDefaultPrevented()) return;

			this.manager.showModal(this);
			this.escape();

			this.tab();

			this.options.loading && this.loading();
		},

		hide: function (e) {
			e && e.preventDefault();
			this._showAfterHide = false;

			e = $.Event('hide');

			this.$element.trigger(e);

			if (!this.isShown || e.isDefaultPrevented()) return;

			this.isShown = false;
			this.isHiding = true;
			this.cancelTransition("show");

			this.escape();

			this.tab();

			this.isLoading && this.loading();

			$(document).off('focusin.modal');

			this.$element
				.removeClass('in')
				.removeClass('animated')
				.removeClass(this.options.attentionAnimation)
				.removeClass('modal-overflow')
				.attr('aria-hidden', true);

			$.support.transition && this.$element.hasClass('fade') ?
				this.hideWithTransition() :
				this.hideModal();
		},

		layout: function () {
			var prop = this.options.height ? 'height' : 'max-height',
				value = this.options.height || this.options.maxHeight,
				minHeight = this.options.minHeight;

			if (this.options.width){
				this.$element.css('width', this.options.width);

				var that = this;
				this.$element.css('margin-left', function () {
					if (/%/ig.test(that.options.width)){
						return -(parseInt(that.options.width) / 2) + '%';
					} else {
						return -($(this).width() / 2) + 'px';
					}
				});
			} else {
				this.$element.css('width', '');
				this.$element.css('margin-left', '');
			}

			this.$element.find('.modal-body')
				.css('overflow', '')
				.css(prop, '')
				.css('min-height', '');

			if (value){
				this.$element.find('.modal-body')
					.css('overflow', 'auto')
					.css(prop, value);
			}

			if (minHeight) {
				this.$element.find('.modal-body')
					.css('min-height', minHeight);
			}

			var modalOverflow = $(window).height() - 10 < this.$element.height();

			if (modalOverflow || this.options.modalOverflow) {
				this.$element
					.css('margin-top', 0)
					.addClass('modal-overflow');
			} else {
				this.$element
					.css('margin-top', 0 - this.$element.height() / 2)
					.removeClass('modal-overflow');
			}
		},

		tab: function () {
			var that = this;

			if (this.isShown && this.options.consumeTab) {
				this.$element.on('keydown.tabindex.modal', '[data-tabindex]', function (e) {
			    	if (e.keyCode && e.keyCode == 9){
						var elements = [],
							tabindex = Number($(this).data('tabindex'));

						that.$element.find('[data-tabindex]:enabled:visible:not([readonly])').each(function (ev) {
							elements.push(Number($(this).data('tabindex')));
						});
						elements.sort(function(a,b){return a-b});

						var arrayPos = $.inArray(tabindex, elements);
						if (!e.shiftKey){
						 		arrayPos < elements.length-1 ?
									that.$element.find('[data-tabindex='+elements[arrayPos+1]+']').focus() :
									that.$element.find('[data-tabindex='+elements[0]+']').focus();
							} else {
								arrayPos == 0 ?
									that.$element.find('[data-tabindex='+elements[elements.length-1]+']').focus() :
									that.$element.find('[data-tabindex='+elements[arrayPos-1]+']').focus();
							}

						e.preventDefault();
					}
				});
			} else if (!this.isShown) {
				this.$element.off('keydown.tabindex.modal');
			}
		},

		escape: function () {
			var that = this;
			if (this.isShown && this.options.keyboard) {
				if (!this.$element.attr('tabindex')) this.$element.attr('tabindex', -1);

				this.$element.on('keyup.dismiss.modal', function (e) {
					e.which == 27 && that.hide();
				});
			} else if (!this.isShown) {
				this.$element.off('keyup.dismiss.modal')
			}
		},

		// Animation events are advisory: never let a missing event own UI lifetime.
		cancelTransition: function (name) {
			if (this._transitions[name]) this._transitions[name]();
		},

		waitForTransition: function ($element, name, callback) {
			this.cancelTransition(name);
			var that = this, finished = false, timer;
			var event = $.support.transition && $.support.transition.end;
			function cancel() {
				finished = true;
				clearTimeout(timer);
				if (event) $element.off(event, complete);
				delete that._transitions[name];
			}
			function complete(e) {
				if (finished || (e && e.target !== $element[0])) return;
				cancel();
				callback();
			}
			this._transitions[name] = cancel;
			if (event && $element.hasClass('fade')) {
				$element.on(event, complete);
				timer = setTimeout(complete, 500);
			} else {
				complete();
			}
		},

		hideWithTransition: function () {
			this.waitForTransition(this.$element, 'hide', $.proxy(this.hideModal, this));
		},

		hideModal: function () {
			if (!this.isHiding || this._destroyed) return;
			var prop = this.options.height ? 'height' : 'max-height';
			var value = this.options.height || this.options.maxHeight;

			if (value){
				this.$element.find('.modal-body')
					.css('overflow', '')
					.css(prop, '');
			}

			this.$element.hide();
			try {
				this.$element.trigger('hidden');
			} finally {
				// Also finish if an application event handler throws before the manager.
				this.destroy(true);
				if (this._showAfterHide && !this.$element.data('modal')) {
					this._showAfterHide = false;
					this.$element.modal($.extend({}, this.options, { show: false })).modal('show');
				}
			}
		},

		removeLoading: function () {
			this.cancelTransition("loading");
			if (this.$loading) this.$loading.remove();
			this.$loading = null;
			this.isLoading = false;
		},

		loading: function (callback) {
			callback = callback || function () {};

			var animate = this.$element.hasClass('fade') ? 'fade' : '';

			if (!this.isLoading) {
				var doAnimate = $.support.transition && animate;

				this.$loading = $('<div class="loading-mask ' + animate + '">')
					.append(this.options.spinner)
					.appendTo(this.$element);

				if (doAnimate) this.$loading[0].offsetWidth; // force reflow

				this.$loading.addClass('in');

				this.isLoading = true;

				doAnimate ?
					this.waitForTransition(this.$loading, "loading", callback) :
					callback();

			} else if (this.isLoading && this.$loading) {
				this.$loading.removeClass('in');

				var that = this;
				$.support.transition && this.$element.hasClass('fade')?
					this.waitForTransition(this.$loading, "loading", function () { that.removeLoading() }) :
					that.removeLoading();

			} else if (callback) {
				callback(this.isLoading);
			}
		},

		focus: function () {
			var $focusElem = this.$element.find(this.options.focusOn);

			$focusElem = $focusElem.length ? $focusElem : this.$element;

			$focusElem.focus();
		},

		attention: function (){
			// NOTE: transitionEnd with keyframes causes odd behaviour

			if (this.options.attentionAnimation){
				this.$element
					.removeClass('animated')
					.removeClass(this.options.attentionAnimation);

				var that = this;

				setTimeout(function () {
					that.$element
						.addClass('animated')
						.addClass(that.options.attentionAnimation);
				}, 0);
			}


			this.focus();
		},


		destroy: function (force) {
			if (this._destroyed || this._destroying) return;
			this._destroying = true;
			var e = $.Event('destroy'), eventError;
			try { this.$element.trigger(e); }
			catch (error) { eventError = error; }
			this._destroying = false;
			// A completed hide must release its layers even if a listener fails/vetoes.
			if (!force && (e.isDefaultPrevented() || eventError)) {
				if (eventError) throw eventError;
				return;
			}

			this._destroyed = true;
			this.isShown = this.isHiding = false;
			for (var name in this._transitions) this.cancelTransition(name);
			this.removeLoading();
			this.$element
				.off('.modal')
				.removeData('modal')
				.removeClass('in')
				.hide()
				.attr('aria-hidden', true);

			// Move the dialog before removing its wrapper; never discard its bindings.
			if (this.$parent.length && $.contains(document.documentElement, this.$parent[0])) {
				this.$element.appendTo(this.$parent);
			} else {
				this.$element.detach();
			}
			this.manager.destroyModal(this);
			this.$element.trigger('destroyed');
			if (eventError) throw eventError;
		}
	};


	/* MODAL PLUGIN DEFINITION
	* ======================= */

	$.fn.modal = function (option, args) {
		return this.each(function () {
			var $this = $(this),
				data = $this.data('modal'),
				options = $.extend({}, $.fn.modal.defaults, $this.data(), typeof option == 'object' && option);

			if (!data) $this.data('modal', (data = new Modal(this, options)));
			if (typeof option == 'string') data[option].apply(data, [].concat(args));
			else if (options.show) data.show()
		})
	};

	$.fn.modal.defaults = {
		keyboard: true,
		backdrop: true,
		loading: false,
		show: true,
		width: null,
		height: null,
		maxHeight: null,
		minHeight: null,
		modalOverflow: false,
		consumeTab: true,
		focusOn: null,
		replace: false,
		resize: false,
		attentionAnimation: 'shake',
		manager: 'body',
		spinner: '<div class="loading-spinner" style="width: 200px; margin-left: -100px;"><div class="progress progress-striped active"><div class="bar" style="width: 100%;"></div></div></div>',
		backdropTemplate: '<div class="modal-backdrop" />'
	};

	$.fn.modal.Constructor = Modal;


	/* MODAL DATA-API
	* ============== */

	$(function () {
		$(document).off('click.modal').on('click.modal.data-api', '[data-toggle="modal"]', function ( e ) {
			var $this = $(this),
				href = $this.attr('href'),
				$target = $($this.attr('data-target') || (href && href.replace(/.*(?=#[^\s]+$)/, ''))), //strip for ie7
				option = $target.data('modal') ? 'toggle' : $.extend({ remote: !/#/.test(href) && href }, $target.data(), $this.data());

			e.preventDefault();
			$target
				.modal(option)
				.one('hide', function () {
					$this.focus();
				})
		});
	});

}(window.jQuery);
