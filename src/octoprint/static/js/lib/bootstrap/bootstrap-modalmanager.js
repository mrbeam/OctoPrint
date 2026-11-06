/* ===========================================================
 * bootstrap-modalmanager.js v2.2.5
 * ===========================================================
 * Copyright 2012 Jordan Schroter.
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

	/* MODAL MANAGER CLASS DEFINITION
	* ====================== */

	var ModalManager = function (element, options) {
		this.init(element, options);
	};

	ModalManager.prototype = {

		constructor: ModalManager,

		init: function (element, options) {
			this.$element = $(element);
			this.options = $.extend({}, $.fn.modalmanager.defaults, this.$element.data(), typeof options == 'object' && options);
			this.stack = [];
			this.backdropCount = 0;

			// Recover layers left by removed dialogs or older, interrupted lifecycles.
			// Ownership, not opacity, decides whether an overlay may be removed.
			var manager = this;
			this._reconcileTimer = setInterval(function () { manager.reconcile(); }, 1000);

			if (this.options.resize) {
				var resizeTimeout,
					that = this;

				$(window).on('resize.modal', function(){
					resizeTimeout && clearTimeout(resizeTimeout);
					resizeTimeout = setTimeout(function(){
						for (var i = 0; i < that.stack.length; i++){
							that.stack[i].isShown && that.stack[i].layout();
						}
					}, 10);
				});
			}
		},

		createModal: function (element, options) {
			$(element).modal($.extend({ manager: this }, options));
		},

		appendModal: function (modal) {
			if (this.getIndexOfModal(modal) !== -1) return;
			this.stack.push(modal);
		},

		showModal: function (modal) {
			var that = this;
			var showModal = function(){
				if (modal._destroyed || modal.isHiding || modal.isShown) return;
				modal.isShown = true;

				var transition = $.support.transition && modal.$element.hasClass('fade');

				that.$element
					.toggleClass('modal-open', that.hasOpenModal())
					.toggleClass('page-overflow', $(window).height() < that.$element.height());


				modal.$container = that.createContainer(modal);

				modal.$element.appendTo(modal.$container);

				that.backdrop(modal, function () {
					if (!modal.isShown || modal._destroyed) return;
					modal.$element.show();

					if (transition) {
						//modal.$element[0].style.display = 'run-in';
						modal.$element[0].offsetWidth;
						//modal.$element.one($.support.transition.end, function () { modal.$element[0].style.display = 'block' });
					}

					modal.layout();

					modal.$element
						.addClass('in')
						.attr('aria-hidden', false);

					var complete = function () {
						if (!modal.isShown || modal._destroyed) return;
						that.setFocus();
						modal.$element.trigger('shown');
					};

					transition ?
						modal.waitForTransition(modal.$element, "show", complete) :
						complete();
				});
			};

			modal.options.replace ?
				that.replace(showModal) :
				showModal();
		},

		getOpenModals: function () {
			var openModals = [];
			for (var i = 0; i < this.stack.length; i++){
				if (this.stack[i].isShown) openModals.push(this.stack[i]);
			}

			return openModals;
		},

		hasOpenModal: function () {
			return this.getOpenModals().length > 0;
		},

		setFocus: function () {
			var topModal;

			for (var i = 0; i < this.stack.length; i++){
				if (this.stack[i].isShown) topModal = this.stack[i];
			}

			if (!topModal) return;

			topModal.focus();
		},

		destroyModal: function (modal) {
			modal.$element.off('.modalmanager');
			if (modal.$backdrop) this.removeBackdrop(modal);
			var index = this.getIndexOfModal(modal);
			if (index !== -1) this.stack.splice(index, 1);
			this.backdropCount = this.countBackdrops();

			var hasOpenModal = this.hasOpenModal();

			this.$element.toggleClass('modal-open', hasOpenModal);

			if (!hasOpenModal){
				this.$element.removeClass('page-overflow');
			}

			this.removeContainer(modal);

			this.setFocus();
		},

		getModalAt: function (index) {
			return this.stack[index];
		},

		getIndexOfModal: function (modal) {
			for (var i = 0; i < this.stack.length; i++){
				if (modal === this.stack[i]) return i;
			}
			return -1;
		},

		replace: function (callback) {
			var topModal;

			for (var i = 0; i < this.stack.length; i++){
				if (this.stack[i].isShown) topModal = this.stack[i];
			}

			if (topModal) {
				this.$backdropHandle = topModal.$backdrop;
				topModal.$backdrop = null;

				callback && topModal.$element.one('hidden',
					targetIsSelf( $.proxy(callback, this) ));

				topModal.hide();
			} else if (callback) {
				callback();
			}
		},

		removeBackdrop: function (modal) {
			if (modal.$backdrop) modal.$backdrop.remove();
			modal.$backdrop = null;
		},

		createBackdrop: function (animate, tmpl) {
			var $backdrop;

			if (!this.$backdropHandle) {
				$backdrop = $(tmpl)
					.addClass(animate)
					.appendTo(this.$element);
			} else {
				$backdrop = this.$backdropHandle;
				$backdrop.off('.modalmanager');
				this.$backdropHandle = null;
				this.isLoading && this.removeSpinner();
			}

			return $backdrop;
		},

		removeContainer: function (modal) {
			if (modal.$container) modal.$container.remove();
			modal.$container = null;
		},

		createContainer: function (modal) {
			var $container;

			$container = $('<div class="modal-scrollable">')
				.css('z-index', getzIndex('modal', this.getOpenModals().length))
				.appendTo(this.$element);

			if (modal && modal.options.backdrop != 'static') {
				$container.on('click.modal', targetIsSelf(function (e) {
					modal.hide();
				}));
			} else if (modal) {
				$container.on('click.modal', targetIsSelf(function (e) {
					modal.attention();
				}));
			}

			return $container;

		},

		backdrop: function (modal, callback) {
			this.backdropCount = this.countBackdrops();
			if (modal.isShown && modal.options.backdrop &&
				this.backdropCount < this.options.backdropLimit) {
				if (!modal.$backdrop) {
					modal.$backdrop = this.createBackdrop(
						modal.$element.hasClass('fade') ? 'fade' : '', modal.options.backdropTemplate);
				}
				modal.$backdrop.css('z-index', getzIndex('backdrop', this.getOpenModals().length)).addClass('in');
			} else if (!modal.isShown) {
				this.removeBackdrop(modal);
			}
			this.backdropCount = this.countBackdrops();
			// Display the dialog immediately, even if CSS transitions never run.
			if (callback) callback();
		},

		countBackdrops: function () {
			var count = 0;
			for (var i = 0; i < this.stack.length; i++) {
				if (this.stack[i].$backdrop && this.stack[i].$backdrop.parent().length) count++;
			}
			return count;
		},

		reconcile: function () {
			var that = this, owned = [], unknownVisible = false;
			// A dialog removed directly by application code must not strand its layers.
			this.stack.slice().forEach(function (modal) {
				if (!$.contains(document.documentElement, modal.$element[0]) ||
					(modal.isShown && modal.$element.css('display') === 'none')) {
					try { modal.destroy(true); }
					catch (error) { if (window.console) window.console.error('Modal recovery:', error); }
				}
				if (modal.$container) owned.push(modal.$container[0]);
				if (modal.$backdrop) owned.push(modal.$backdrop[0]);
			});
			if (this.$backdropHandle) owned.push(this.$backdropHandle[0]);
			if (this.$spinner) owned.push(this.$spinner[0]);
			this.$element.children('.modal-scrollable').each(function () {
				if ($.inArray(this, owned) !== -1) return;
				var $container = $(this), $dialogs = $container.find('.modal');
				// Do not dismiss a live dialog owned by another integration.
				if ($dialogs.filter(function () {
					return $(this).hasClass('in') || $(this).css('display') !== 'none';
				}).length) { unknownVisible = true; return; }
				$dialogs.detach().appendTo(that.$element);
				$container.remove();
			});
			if (!unknownVisible) {
				this.$element.children('.modal-backdrop').each(function () {
					if ($.inArray(this, owned) === -1) $(this).remove();
				});
			}
			this.backdropCount = this.countBackdrops();
			var blocking = this.hasOpenModal() || this.isLoading || unknownVisible;
			this.$element.toggleClass('modal-open', !!blocking);
			if (!blocking) this.$element.removeClass('page-overflow');
		},

		removeSpinner: function(){
			this.$spinner && this.$spinner.remove();
			this.$spinner = null;
			this.isLoading = false;
		},

		removeLoading: function () {
			this.$backdropHandle && this.$backdropHandle.remove();
			this.$backdropHandle = null;
			this.removeSpinner();
			this.$element.toggleClass("modal-open", this.hasOpenModal());
			if (!this.hasOpenModal()) this.$element.removeClass("page-overflow");
		},

		loading: function (callback) {
			callback = callback || function () { };

			this.$element
				.toggleClass('modal-open', !this.isLoading || this.hasOpenModal())
				.toggleClass('page-overflow', $(window).height() < this.$element.height());

			if (!this.isLoading) {

				this.$backdropHandle = this.createBackdrop('fade', this.options.backdropTemplate);

				this.$backdropHandle[0].offsetWidth; // force reflow

				var openModals = this.getOpenModals();

				this.$backdropHandle
					.css('z-index', getzIndex('backdrop', openModals.length + 1))
					.addClass('in');

				var $spinner = $(this.options.spinner)
					.css('z-index', getzIndex('modal', openModals.length + 1))
					.appendTo(this.$element)
					.addClass('in');

				this.$spinner = $(this.createContainer())
					.append($spinner)
					.on('click.modalmanager', $.proxy(this.loading, this));

				this.isLoading = true;

				callback();

			} else if (this.isLoading && this.$backdropHandle) {
				this.$backdropHandle.removeClass('in');

				this.removeLoading();

			} else if (callback) {
				callback(this.isLoading);
			}
		}
	};

	/* PRIVATE METHODS
	* ======================= */

	// computes and caches the zindexes
	var getzIndex = (function () {
		var zIndexFactor,
			baseIndex = {};

		return function (type, pos) {

			if (typeof zIndexFactor === 'undefined'){
				var $baseModal = $('<div class="modal hide" />').appendTo('body'),
					$baseBackdrop = $('<div class="modal-backdrop hide" />').appendTo('body');

				baseIndex['modal'] = +$baseModal.css('z-index');
				baseIndex['backdrop'] = +$baseBackdrop.css('z-index');
				zIndexFactor = baseIndex['modal'] - baseIndex['backdrop'];

				$baseModal.remove();
				$baseBackdrop.remove();
				$baseBackdrop = $baseModal = null;
			}

			return baseIndex[type] + (zIndexFactor * pos);

		}
	}());

	// make sure the event target is the modal itself in order to prevent
	// other components such as tabsfrom triggering the modal manager.
	// if Boostsrap namespaced events, this would not be needed.
	function targetIsSelf(callback){
		return function (e) {
			if (e && this === e.target){
				return callback.apply(this, arguments);
			}
		}
	}


	/* MODAL MANAGER PLUGIN DEFINITION
	* ======================= */

	$.fn.modalmanager = function (option, args) {
		return this.each(function () {
			var $this = $(this),
				data = $this.data('modalmanager');

			if (!data) $this.data('modalmanager', (data = new ModalManager(this, option)));
			if (typeof option === 'string') data[option].apply(data, [].concat(args))
		})
	};

	$.fn.modalmanager.defaults = {
		backdropLimit: 999,
		resize: true,
		spinner: '<div class="loading-spinner fade" style="width: 200px; margin-left: -100px;"><div class="progress progress-striped active"><div class="bar" style="width: 100%;"></div></div></div>',
		backdropTemplate: '<div class="modal-backdrop" />'
	};

	$.fn.modalmanager.Constructor = ModalManager

	// ModalManager handles the modal-open class so we need
	// to remove conflicting bootstrap 3 event handlers
	$(function () {
		$(document).off('show.bs.modal').off('hidden.bs.modal');
	});

}(jQuery);
