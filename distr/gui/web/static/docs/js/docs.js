(function() {
  'use strict';

  var sectionsContainer = document.getElementById('docs-sections');
  var sectionNav = document.getElementById('docs-section-nav');
  var tryModal = document.getElementById('try-modal');
  var tryModalTitle = document.getElementById('try-modal-title');
  var tryModalParams = document.getElementById('try-modal-params');
  var tryModalBody = document.getElementById('try-modal-body');
  var tryModalSend = document.getElementById('try-modal-send');
  var tryModalResult = document.getElementById('try-modal-result');
  var tryModalResponse = document.getElementById('try-modal-response');
  var currentEndpoint = null;

  // Close modal
  document.getElementById('try-modal-close').onclick = closeModal;
  function closeModal() {
    tryModal.classList.add('hidden');
    currentEndpoint = null;
  }

  // Fetch and render
  fetch('/docs/api/endpoints')
    .then(function(r) { return r.json(); })
    .then(function(data) { renderSections(data.sections); })
    .catch(function(e) {
      sectionsContainer.innerHTML = '<p class="text-red-400">Failed to load API docs: ' + e.message + '</p>';
    });

  function renderSections(sections) {
    sectionsContainer.innerHTML = '';
    sectionNav.innerHTML = '';

    sections.forEach(function(section) {
      // Nav pill
      var pill = document.createElement('a');
      pill.className = 'section-pill';
      pill.href = '#section-' + slugify(section.section);
      pill.textContent = section.section;
      sectionNav.appendChild(pill);

      // Section container
      var div = document.createElement('div');
      div.className = 'docs-section';
      div.id = 'section-' + slugify(section.section);

      var title = document.createElement('h2');
      title.className = 'docs-section-title';
      title.textContent = section.section;
      div.appendChild(title);

      section.endpoints.forEach(function(ep) {
        div.appendChild(buildEndpointCard(ep));
      });

      sectionsContainer.appendChild(div);
    });
  }

  function buildEndpointCard(ep) {
    var card = document.createElement('div');
    card.className = 'endpoint-card';

    // Header (clickable to expand)
    var header = document.createElement('div');
    header.className = 'endpoint-header';
    header.innerHTML =
      '<span class="endpoint-chevron">&#9654;</span>' +
      '<span class="method-badge method-' + ep.method + '">' + ep.method + '</span>' +
      '<span class="endpoint-path">' + escHtml(ep.path) + '</span>' +
      '<span class="endpoint-summary">' + escHtml(ep.summary) + '</span>';
    header.onclick = function() { card.classList.toggle('open'); };
    card.appendChild(header);

    // Body
    var body = document.createElement('div');
    body.className = 'endpoint-body';

    // Description
    if (ep.description) {
      var desc = document.createElement('p');
      desc.className = 'endpoint-desc';
      desc.textContent = ep.description;
      body.appendChild(desc);
    }

    // URL params table
    if (ep.params && ep.params.length) {
      body.appendChild(buildLabel('URL Parameters'));
      body.appendChild(buildSchemaTable(ep.params));
    }

    // Body schema table
    if (ep.body && typeof ep.body === 'object') {
      body.appendChild(buildLabel('Request Body'));
      var rows = Object.keys(ep.body).map(function(k) {
        var f = ep.body[k];
        return { name: k, type: f.type || 'string', required: f.required, description: f.description || '' };
      });
      body.appendChild(buildSchemaTable(rows));
    }

    // Curl block
    if (ep.curl) {
      body.appendChild(buildLabel('cURL'));
      body.appendChild(buildCurlBlock(ep.curl));
    }

    // Response example
    if (ep.response_example) {
      body.appendChild(buildLabel('Response Example'));
      var respBlock = document.createElement('pre');
      respBlock.className = 'response-block';
      try {
        respBlock.textContent = JSON.stringify(JSON.parse(ep.response_example), null, 2);
      } catch(_) {
        respBlock.textContent = ep.response_example;
      }
      body.appendChild(respBlock);
    }

    // Action buttons
    var actions = document.createElement('div');
    actions.className = 'endpoint-actions';
    var tryBtn = document.createElement('button');
    tryBtn.className = 'try-btn';
    tryBtn.textContent = 'Try it';
    tryBtn.onclick = function(e) { e.stopPropagation(); openTryModal(ep); };
    actions.appendChild(tryBtn);
    body.appendChild(actions);

    card.appendChild(body);
    return card;
  }

  function buildLabel(text) {
    var lbl = document.createElement('div');
    lbl.style.cssText = 'font-size:0.75rem;color:#94a3b8;font-weight:600;margin-bottom:0.35rem;margin-top:0.5rem;text-transform:uppercase;letter-spacing:0.05em;';
    lbl.textContent = text;
    return lbl;
  }

  function buildSchemaTable(rows) {
    var table = document.createElement('table');
    table.className = 'schema-table';
    var thead = '<tr><th>Name</th><th>Type</th><th>Required</th><th>Description</th></tr>';
    var tbody = rows.map(function(r) {
      var req = r.required ? '<span class="required-badge">required</span>' : 'optional';
      return '<tr><td><code>' + escHtml(r.name) + '</code></td><td>' + escHtml(r.type) + '</td><td>' + req + '</td><td>' + escHtml(r.description) + '</td></tr>';
    }).join('');
    table.innerHTML = '<thead>' + thead + '</thead><tbody>' + tbody + '</tbody>';
    return table;
  }

  function buildCurlBlock(curl) {
    var wrap = document.createElement('div');
    wrap.className = 'curl-block';
    var pre = document.createElement('pre');
    pre.textContent = curl;
    wrap.appendChild(pre);

    var btn = document.createElement('button');
    btn.className = 'curl-copy-btn';
    btn.textContent = 'Copy';
    btn.onclick = function(e) {
      e.stopPropagation();
      navigator.clipboard.writeText(curl).then(function() {
        btn.textContent = 'Copied';
        btn.classList.add('copied');
        setTimeout(function() { btn.textContent = 'Copy'; btn.classList.remove('copied'); }, 1500);
      });
    };
    wrap.appendChild(btn);
    return wrap;
  }

  // ── Try-it modal ──────────────────────────────────────────────

  function openTryModal(ep) {
    currentEndpoint = ep;
    tryModalTitle.textContent = ep.method + ' ' + ep.path;
    tryModalParams.innerHTML = '';
    tryModalBody.innerHTML = '';
    tryModalResult.classList.add('hidden');
    tryModalResponse.textContent = '';

    // URL params
    if (ep.params && ep.params.length) {
      var h = document.createElement('div');
      h.style.cssText = 'font-size:0.75rem;color:#94a3b8;font-weight:600;margin-bottom:0.5rem;text-transform:uppercase;';
      h.textContent = 'URL Parameters';
      tryModalParams.appendChild(h);
      ep.params.forEach(function(p) {
        tryModalParams.appendChild(buildField('param-' + p.name, p.name, p.description, p.required));
      });
    }

    // Body fields
    if (ep.body && typeof ep.body === 'object') {
      var h2 = document.createElement('div');
      h2.style.cssText = 'font-size:0.75rem;color:#94a3b8;font-weight:600;margin-bottom:0.5rem;text-transform:uppercase;';
      h2.textContent = 'Request Body';
      tryModalBody.appendChild(h2);
      Object.keys(ep.body).forEach(function(k) {
        var f = ep.body[k];
        var isTextarea = (f.type === 'array' || k === 'instruction' || k === 'steps');
        tryModalBody.appendChild(buildField('body-' + k, k + (f.required ? ' *' : ''), f.description || '', f.required, isTextarea));
      });
    }

    tryModal.classList.remove('hidden');
  }

  function buildField(id, label, hint, required, textarea) {
    var div = document.createElement('div');
    div.className = 'try-field';
    var lbl = document.createElement('label');
    lbl.setAttribute('for', id);
    lbl.textContent = label;
    div.appendChild(lbl);

    var input;
    if (textarea) {
      input = document.createElement('textarea');
      input.rows = 3;
      input.placeholder = 'JSON array or object...';
    } else {
      input = document.createElement('input');
      input.type = 'text';
    }
    input.id = id;
    div.appendChild(input);

    if (hint) {
      var h = document.createElement('div');
      h.className = 'field-hint';
      h.textContent = hint;
      div.appendChild(h);
    }
    return div;
  }

  // Send request
  tryModalSend.onclick = function() {
    if (!currentEndpoint) return;
    var ep = currentEndpoint;

    // Build URL with param substitution
    var url = ep.path;
    if (ep.params) {
      ep.params.forEach(function(p) {
        var val = (document.getElementById('param-' + p.name) || {}).value || '';
        url = url.replace('{' + p.name + '}', encodeURIComponent(val));
      });
    }

    // Build body
    var bodyObj = null;
    if (ep.body && typeof ep.body === 'object') {
      bodyObj = {};
      Object.keys(ep.body).forEach(function(k) {
        var el = document.getElementById('body-' + k);
        if (!el) return;
        var val = el.value.trim();
        if (!val) return;
        var f = ep.body[k];
        if (f.type === 'boolean') {
          bodyObj[k] = val === 'true';
        } else if (f.type === 'int' || f.type === 'integer' || f.type === 'number') {
          bodyObj[k] = Number(val);
        } else if (f.type === 'array' || f.type === 'object') {
          try { bodyObj[k] = JSON.parse(val); } catch(_) { bodyObj[k] = val; }
        } else {
          bodyObj[k] = val;
        }
      });
      if (Object.keys(bodyObj).length === 0) bodyObj = null;
    }

    var fetchOpts = { method: ep.method, headers: {} };
    if (bodyObj && (ep.method === 'POST' || ep.method === 'PUT' || ep.method === 'PATCH')) {
      fetchOpts.headers['Content-Type'] = 'application/json';
      fetchOpts.body = JSON.stringify(bodyObj);
    }

    tryModalResponse.textContent = 'Sending...';
    tryModalResult.classList.remove('hidden');

    fetch(url, fetchOpts)
      .then(function(r) {
        return r.text().then(function(txt) {
          var status = r.status + ' ' + r.statusText;
          try {
            var json = JSON.parse(txt);
            return status + '\n\n' + JSON.stringify(json, null, 2);
          } catch(_) {
            return status + '\n\n' + txt;
          }
        });
      })
      .then(function(result) { tryModalResponse.textContent = result; })
      .catch(function(e) { tryModalResponse.textContent = 'Error: ' + e.message; });
  };

  // Helpers
  function slugify(s) { return s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, ''); }
  function escHtml(s) {
    if (!s) return '';
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

})();
