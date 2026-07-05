/**
 * media-uploader.js — reusable drag-and-drop image uploader
 *
 * Pairs with app/templates/superadmin/_media_uploader.html and
 * app/static/css/media-uploader.css. Auto-initializes every
 * `[data-uploader]` element on the page — no per-page wiring needed.
 *
 * Expected markup contract (see the include for the full structure):
 *   [data-uploader]
 *     data-upload-url    — POST endpoint, expects multipart field "file"
 *     data-category       — sent as form field "category"
 *     data-target-input   — CSS selector of the hidden/visible form field
 *                            that should receive the resulting URL
 *     data-max-size        — max bytes (default 5MB)
 *     data-accept           — accept attribute value
 *     [data-role="dropzone"]     — empty-state click/drag target
 *     [data-role="file-input"]   — the actual <input type="file">
 *     [data-role="preview"]      — filled-state container
 *     [data-role="preview-img"]  — <img> inside preview
 *     [data-role="progress"]     — progress bar wrapper
 *     [data-role="progress-bar"] — progress bar fill
 *     [data-role="status"]       — status text line
 *     [data-action="replace"]    — re-opens file picker
 *     [data-action="remove"]     — clears the field back to empty state
 */
(function () {
  'use strict';

  const DEFAULT_ACCEPT = 'image/png,image/jpeg,image/webp,image/gif';
  const DEFAULT_MAX_SIZE = 5 * 1024 * 1024; // 5MB

  function humanSize(bytes) {
    if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + 'MB';
    if (bytes >= 1024) return Math.round(bytes / 1024) + 'KB';
    return bytes + 'B';
  }

  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : '';
  }

  class MediaUploader {
    constructor(root) {
      this.root = root;
      this.uploadUrl = root.dataset.uploadUrl;
      this.category = root.dataset.category || '';
      this.maxSize = parseInt(root.dataset.maxSize || DEFAULT_MAX_SIZE, 10);
      this.accept = root.dataset.accept || DEFAULT_ACCEPT;
      this.allowedExts = this.accept
        .split(',')
        .map((m) => m.trim().split('/')[1])
        .filter(Boolean);

      this.targetInput = root.dataset.targetInput
        ? document.querySelector(root.dataset.targetInput)
        : null;

      this.dropzone = root.querySelector('[data-role="dropzone"]');
      this.fileInput = root.querySelector('[data-role="file-input"]');
      this.preview = root.querySelector('[data-role="preview"]');
      this.previewImg = root.querySelector('[data-role="preview-img"]');
      this.progress = root.querySelector('[data-role="progress"]');
      this.progressBar = root.querySelector('[data-role="progress-bar"]');
      this.status = root.querySelector('[data-role="status"]');
      this.replaceBtn = root.querySelector('[data-action="replace"]');
      this.removeBtn = root.querySelector('[data-action="remove"]');

      this._bind();
    }

    _bind() {
      if (this.fileInput) {
        this.fileInput.addEventListener('change', () => {
          const file = this.fileInput.files && this.fileInput.files[0];
          if (file) this._handleFile(file);
        });
      }

      if (this.dropzone) {
        ['dragenter', 'dragover'].forEach((evt) =>
          this.dropzone.addEventListener(evt, (e) => {
            e.preventDefault();
            this.root.classList.add('is-dragover');
          })
        );
        ['dragleave', 'drop'].forEach((evt) =>
          this.dropzone.addEventListener(evt, (e) => {
            e.preventDefault();
            this.root.classList.remove('is-dragover');
          })
        );
        this.dropzone.addEventListener('drop', (e) => {
          const file = e.dataTransfer.files && e.dataTransfer.files[0];
          if (file) this._handleFile(file);
        });
      }

      if (this.replaceBtn) {
        this.replaceBtn.addEventListener('click', () => this.fileInput.click());
      }

      if (this.removeBtn) {
        this.removeBtn.addEventListener('click', () => this._reset());
      }
    }

    _setStatus(text, kind) {
      if (!this.status) return;
      this.status.textContent = text || '';
      this.status.className = 'uploader-status' + (kind ? ' is-' + kind : '');
      if (kind === 'uploading') {
        const spinner = document.createElement('span');
        spinner.className = 'spinner';
        this.status.prepend(spinner);
      }
    }

    _validate(file) {
      const ext = (file.name.split('.').pop() || '').toLowerCase();
      const extOk = this.allowedExts.length === 0 || this.allowedExts.includes(ext) ||
        this.allowedExts.includes(ext === 'jpg' ? 'jpeg' : ext);
      if (!extOk) {
        return `Unsupported file type. Allowed: ${this.allowedExts.join(', ').toUpperCase()}.`;
      }
      if (file.size > this.maxSize) {
        return `File is too large (${humanSize(file.size)}). Max ${humanSize(this.maxSize)}.`;
      }
      return null;
    }

    _handleFile(file) {
      const error = this._validate(file);
      if (error) {
        this._setStatus(error, 'error');
        return;
      }

      // Optimistic local preview while the upload is in flight.
      const localUrl = URL.createObjectURL(file);
      this._showPreview(localUrl);
      this._setStatus('Uploading…', 'uploading');
      if (this.progress) this.progress.hidden = false;
      if (this.progressBar) this.progressBar.style.width = '0%';

      const formData = new FormData();
      formData.append('file', file);
      formData.append('category', this.category);
      formData.append('csrf_token', csrfToken());

      const xhr = new XMLHttpRequest();
      xhr.open('POST', this.uploadUrl, true);

      xhr.upload.addEventListener('progress', (e) => {
        if (!e.lengthComputable || !this.progressBar) return;
        this.progressBar.style.width = Math.round((e.loaded / e.total) * 100) + '%';
      });

      xhr.addEventListener('load', () => {
        if (this.progress) this.progress.hidden = true;
        let data;
        try {
          data = JSON.parse(xhr.responseText);
        } catch (e) {
          data = null;
        }
        if (xhr.status >= 200 && xhr.status < 300 && data && data.success) {
          this._showPreview(data.url);
          if (this.targetInput) {
            this.targetInput.value = data.url;
            this.targetInput.dispatchEvent(new Event('input', { bubbles: true }));
          }
          this._setStatus('Uploaded.', 'success');
        } else {
          const msg = (data && data.error) || 'Upload failed. Please try again.';
          this._setStatus(msg, 'error');
          this._reset({ keepStatus: true });
        }
        URL.revokeObjectURL(localUrl);
      });

      xhr.addEventListener('error', () => {
        if (this.progress) this.progress.hidden = true;
        this._setStatus('Upload failed — check your connection and try again.', 'error');
        this._reset({ keepStatus: true });
        URL.revokeObjectURL(localUrl);
      });

      xhr.send(formData);
    }

    _showPreview(url) {
      if (this.previewImg) this.previewImg.src = url;
      if (this.preview) this.preview.hidden = false;
      if (this.dropzone) this.dropzone.hidden = true;
    }

    _reset(opts) {
      opts = opts || {};
      if (this.preview) this.preview.hidden = true;
      if (this.dropzone) this.dropzone.hidden = false;
      if (this.fileInput) this.fileInput.value = '';
      if (this.targetInput) {
        this.targetInput.value = '';
        this.targetInput.dispatchEvent(new Event('input', { bubbles: true }));
      }
      if (!opts.keepStatus) this._setStatus('', null);
    }
  }

  function init() {
    document.querySelectorAll('[data-uploader]').forEach((root) => {
      if (root.__mediaUploaderInit) return;
      root.__mediaUploaderInit = true;
      new MediaUploader(root);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
