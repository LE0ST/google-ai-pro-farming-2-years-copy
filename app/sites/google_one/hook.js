(() => {
  if (!location.hostname.includes('one.google.com')) return;
  if (window.__HOOK_INSTALLED) return;
  window.__HOOK_INSTALLED = true;
  let currentTest = window.__INIT_TEST || null;
  window.__setDirectTest = (t) => { currentTest = t; };

  function patchTargetNode(node) {
    if (!Array.isArray(node) || !Array.isArray(currentTest?.full_items)) return false;
    let patched = false;
    if (node.length > 12 && Array.isArray(node[12])) {
      if (Array.isArray(node[7]) && Array.isArray(node[7][5]) && node[7][5].length > 0) {
        window.__capturedSubId = node[7][5][0];
      }
      node[12] = currentTest.full_items;
      const targetMode = currentTest.mode || (Array.isArray(currentTest.ear8) ? currentTest.ear8[1] : 5) || 5;
      if (currentTest.ear8) {
        node[7] = currentTest.ear8;
      } else if (Array.isArray(node[7]) && Array.isArray(node[7][5]) && node[7][5].length > 0) {
        node[7][1] = targetMode;
      } else if (window.__capturedSubId) {
        node[7] = [null, targetMode, null, null, null, [window.__capturedSubId]];
      }
      patched = true;
    }
    if (node.length > 10 && Array.isArray(node[10]) && Array.isArray(node[10][0])) {
      node[10] = [currentTest.full_items];
      if (Array.isArray(node[2]) && Array.isArray(node[2][2]) && Array.isArray(node[2][2][8])) {
        node[2][2][8] = [currentTest.full_items];
      }
      if (currentTest.ear8) {
        node[6] = currentTest.ear8;
      }
      patched = true;
    }
    return patched;
  }

  function rewriteFreq(bodyStr) {
    if (typeof bodyStr !== 'string' || !bodyStr.includes('f.req=') || !currentTest) return bodyStr;
    try {
      const parts = bodyStr.split('&');
      for (let i = 0; i < parts.length; i++) {
        if (parts[i].startsWith('f.req=')) {
          const raw = decodeURIComponent(parts[i].slice(6).replace(/\+/g, ' '));
          const outer = JSON.parse(raw);
          let changed = false;
          if (Array.isArray(outer)) {
            for (const group of outer) {
              if (!Array.isArray(group)) continue;
              for (const call of group) {
                if (Array.isArray(call) && call[0] === 'Ngbm' && typeof call[1] === 'string') {
                  const payload = JSON.parse(call[1]);
                  if (Array.isArray(payload)) {
                    if (patchTargetNode(payload[0])) changed = true;
                    if (patchTargetNode(payload[1])) changed = true;
                  }
                  if (changed) {
                    call[1] = JSON.stringify(payload);
                  }
                }
              }
            }
          }
          if (changed) {
            parts[i] = 'f.req=' + encodeURIComponent(JSON.stringify(outer));
            return parts.join('&');
          }
        }
      }
    } catch (e) {}
    return bodyStr;
  }

  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function(body) {
    if (typeof body === 'string' && body.includes('Ngbm')) {
      body = rewriteFreq(body);
    }
    return origSend.call(this, body);
  };

  function rewriteUrl(val) {
    const s = String(val || '');
    if (!s.includes('/eacquire/multiline') || !currentTest) return val;
    try {
      const u = new URL(s, location.origin);
      const earStr = u.searchParams.get('ear');
      if (!earStr) return val;
      const ear = JSON.parse(earStr);
      if (Array.isArray(ear[8]) && Array.isArray(ear[8][5]) && ear[8][5].length > 0) {
        window.__capturedSubId = ear[8][5][0];
      }
      if (Array.isArray(ear[13]) && Array.isArray(currentTest.full_items)) {
        ear[13][0] = currentTest.full_items;
      }
      if (Array.isArray(ear[4]) && typeof ear[4][0] === 'string' && Array.isArray(currentTest.full_items)) {
        try {
          const inner4 = JSON.parse(atob(ear[4][0]));
          if (Array.isArray(inner4) && inner4.length > 8) {
            inner4[8] = [currentTest.full_items];
            ear[4][0] = btoa(JSON.stringify(inner4));
          }
        } catch (e4) {}
      }
      const targetMode = currentTest.mode || (Array.isArray(currentTest.ear8) ? currentTest.ear8[1] : 5) || 5;
      if (currentTest.ear8) {
        ear[8] = currentTest.ear8;
      } else if (Array.isArray(ear[8]) && Array.isArray(ear[8][5]) && ear[8][5].length > 0) {
        ear[8][1] = targetMode;
      } else if (window.__capturedSubId) {
        ear[8] = [null, targetMode, null, null, null, [window.__capturedSubId]];
      }
      ear[24] = null;
      u.searchParams.set('ear', JSON.stringify(ear));
      window.__capturedEarUrl = u.toString();
      return u.toString();
    } catch (e) {
      return val;
    }
  }

  if (window.TrustedTypePolicy && TrustedTypePolicy.prototype.createScriptURL) {
    const origCreate = TrustedTypePolicy.prototype.createScriptURL;
    TrustedTypePolicy.prototype.createScriptURL = function(input, ...args) {
      return origCreate.call(this, rewriteUrl(input), ...args);
    };
  }
  const frameProto = HTMLIFrameElement.prototype;
  const srcDesc = Object.getOwnPropertyDescriptor(frameProto, 'src');
  if (srcDesc?.set) {
    Object.defineProperty(frameProto, 'src', {
      ...srcDesc,
      set(v) {
        if (typeof v === 'string') v = rewriteUrl(v);
        return srcDesc.set.call(this, v);
      }
    });
  }
  const origSetAttr = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function(name, val) {
    if (this instanceof HTMLIFrameElement && String(name).toLowerCase() === 'src') {
      val = rewriteUrl(String(val));
    }
    return origSetAttr.call(this, name, val);
  };
})();
