(() => {
    'use strict';
    const fileMode = location.protocol === 'file:';
    const localMode = location.protocol === 'http:' && location.hostname === '127.0.0.1';
    const byId = id => document.getElementById(id);
    const form = byId('token-form'), field = byId('tushare-token');
    const submit = byId('start-calculation'), cancel = byId('cancel-pending');
    const statusLine = byId('refresh-status'), title = byId('access-title');
    let status, submitting = false, chartLoad;
    let waiting = false, heldToken = '', attempt = 0, frame, bridgePort;

    function notice(text, error = false) {
        statusLine.textContent = text;
        statusLine.dataset.error = String(error);
    }

    function loadScript(path, version) {
        return new Promise((resolve, reject) => {
            const node = document.createElement('script');
            const url = new URL(path, location.href);
            if (version) url.searchParams.set('v', version);
            node.src = url.href;
            const finish = success => {
                node.remove();
                if (success) resolve();
                else reject(new Error('资源暂不可用'));
            };
            node.onload = () => finish(true);
            node.onerror = () => finish(false);
            document.head.append(node);
        });
    }

    function loadCharts(version) {
        if (chartLoad) return chartLoad;
        chartLoad = (async () => {
            await loadScript('数据/series.js', version);
            await Promise.all(['flows','marketflow','yields'].map(name =>
                loadScript('数据/' + name + '.js', version)));
            await Promise.all(['程序/engine.js','程序/vendor/echarts.min.js'].map(name => loadScript(name)));
            document.body.classList.remove('awaiting-history');
            await loadScript('程序/ui.js');
            return true;
        })().catch(() => {
            document.body.classList.add('awaiting-history');
            chartLoad = null;
            return false;
        });
        return chartLoad;
    }

    function applyStatus(next) {
        status = next;
        form.hidden = next.busy || !next.requiresToken;
        submit.disabled = submitting || next.busy || !next.csrfToken;
        title.textContent = next.busy ? '正在计算图表' : next.requiresToken ? '连接 Tushare' : '数据更新';
        if (!submitting) notice(next.message);
    }

    function discardBridge() {
        if (bridgePort) bridgePort.close();
        if (frame) frame.remove();
        frame = null;
        bridgePort = null;
    }

    function finishFileWait() {
        attempt++;
        heldToken = '';
        waiting = false;
        discardBridge();
        cancel.hidden = true;
        form.hidden = false;
        title.textContent = '连接 Tushare';
    }

    // The two proofs come only from the generated file in this exact project folder.
    // Never send a token to a guessed port, and bind the handoff to the verified document's port.
    async function findService(id) {
        if (!waiting || id !== attempt) return;
        window.PUBLIC_DASHBOARD_SERVICE = null;
        try { await loadScript('程序/本机服务.js', Date.now()); } catch { /* service has not started */ }
        if (!waiting || id !== attempt) return;
        const manifest = window.PUBLIC_DASHBOARD_SERVICE;
        window.PUBLIC_DASHBOARD_SERVICE = null;
        const valid = manifest && Number.isInteger(manifest.port) && manifest.port > 0 && manifest.port <= 65535 &&
            typeof manifest.proof === 'string' && manifest.proof.length >= 24 &&
            typeof manifest.capability === 'string' && manifest.capability.length >= 24;
        if (!valid) {
            setTimeout(() => findService(id), 1300);
            return;
        }
        const origin = 'http://127.0.0.1:' + manifest.port;
        const child = document.createElement('iframe');
        child.hidden = true;
        child.title = '本机计算服务';
        child.referrerPolicy = 'no-referrer';
        let delivered = false;
        const retry = setTimeout(() => {
            window.removeEventListener('message', ready);
            if (id !== attempt || !waiting || delivered) return;
            discardBridge();
            findService(id);
        }, 3400);
        function ready(event) {
            if (!waiting || id !== attempt || event.source !== child.contentWindow ||
                event.origin !== origin || event.data?.type !== 'funds-bridge-ready' ||
                event.data.proof !== manifest.proof || event.ports.length !== 1) return;
            window.removeEventListener('message', ready);
            clearTimeout(retry);
            delivered = true;
            const port = event.ports[0];
            bridgePort = port;
            cancel.hidden = true;
            notice('已连接本目录的计算服务，正在提交。');
            const timeout = setTimeout(() => {
                if (id !== attempt) return;
                finishFileWait();
                notice('尚未收到本机回执。请在启动器打开的看板中查看进度，再决定是否重试。', true);
            }, 15000);
            port.onmessage = reply => {
                const result = reply.data;
                if (id !== attempt || result?.type !== 'funds-configured' || result.proof !== manifest.proof) return;
                clearTimeout(timeout);
                finishFileWait();
                if (result.ok || result.status === 409) {
                    location.replace(origin + '/' + encodeURIComponent('打开图表.html'));
                } else {
                    notice(result.message || '本机服务未接受本次提交，请重新输入。', true);
                }
            };
            port.postMessage({type: 'funds-configure', capability: manifest.capability, token: heldToken});
            heldToken = '';
        }
        window.addEventListener('message', ready);
        frame = child;
        child.src = origin + '/bridge';
        document.body.append(child);
    }

    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (waiting || submitting || (!fileMode && (!localMode || !status?.csrfToken))) return;
        const token = field.value.trim();
        if (!/^[!-~]{1,512}$/.test(token)) {
            field.setCustomValidity('请粘贴完整 Token，检查空格和换行。');
            field.reportValidity();
            field.focus();
            return;
        }
        field.value = '';
        if (fileMode) {
            heldToken = token;
            waiting = true;
            form.hidden = true;
            cancel.hidden = false;
            title.textContent = '正在连接本机计算服务';
            notice('请双击同目录的「打开资金图表.cmd」，并保持本页打开。服务就绪后会自动用刚才的 Token 开始计算。');
            findService(++attempt);
            return;
        }
        submitting = true;
        submit.disabled = true;
        notice('正在提交给本机计算服务…');
        try {
            const response = await fetch('/configure', {
                method: 'POST', mode: 'same-origin', credentials: 'same-origin', redirect: 'error', cache: 'no-store',
                headers: {'Content-Type': 'application/json', 'X-CSRF-Token': status.csrfToken},
                body: JSON.stringify({token})
            });
            const body = await response.json();
            if (response.ok || response.status === 409) {
                form.hidden = true;
                notice(body.message || '已提交，正在读取数据。');
                window.dispatchEvent(new Event('public-status-refresh'));
            } else {
                notice(body.message || '提交失败，请检查 Token 后重试。', true);
            }
        } catch {
            notice('未收到本机服务响应。请查看启动器窗口，并在恢复连接后重试。', true);
        } finally {
            submitting = false;
            submit.disabled = false;
        }
    });

    field.addEventListener('input', () => field.setCustomValidity(''));
    cancel.addEventListener('click', () => {
        finishFileWait();
        notice('已取消，本页暂存的 Token 已清除。');
        field.focus();
    });
    window.addEventListener('pagehide', () => {
        heldToken = '';
        field.value = '';
        waiting = false;
        attempt++;
        discardBridge();
    });
    if (fileMode) {
        byId('access-mode').textContent = '本地文件';
        form.hidden = false;
        notice('输入 Token 后，可运行同目录的「打开资金图表.cmd」启动计算。');
        loadCharts();
    } else if (localMode) {
        byId('access-mode').textContent = '本机服务';
    } else {
        notice('请在自己的电脑上打开本文件，或双击同目录的「打开资金图表.cmd」。');
    }
    window.PublicEntry = {localMode, loadCharts, applyStatus, notice};
})();
