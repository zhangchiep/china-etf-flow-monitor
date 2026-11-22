(() => {
    'use strict';
    const entry = window.PublicEntry;
    if (!entry?.localMode) return;
    let seenVersion, loaded = false, polling = false, requested = false, timer;
    const datesInUse = () => {
        const picker = document.getElementById('calendar-popover');
        return (picker && !picker.hidden) || !!document.querySelector('[data-range-state="loading"]');
    };

    async function poll() {
        clearTimeout(timer);
        if (polling) { requested = true; return; }
        polling = true;
        let delay = 2500;
        try {
            const response = await fetch('/status.json', {
                mode: 'same-origin', credentials: 'same-origin', cache: 'no-store', redirect: 'error'
            });
            if (!response.ok) throw new Error();
            const status = await response.json();
            if (typeof status.requiresToken !== 'boolean' || typeof status.busy !== 'boolean') throw new Error();
            entry.applyStatus(status);
            if (status.hasData && !loaded) {
                loaded = await entry.loadCharts(status.version);
                if (loaded) seenVersion = status.version;
            } else if (loaded && !status.busy && seenVersion !== status.version && !datesInUse()) {
                const target = new URL(location.href);
                target.searchParams.set('v', status.version);
                location.replace(target.href);
                return;
            }
            delay = status.busy ? 1200 : status.requiresToken ? 3500 : 15000;
        } catch {
            entry.notice('本机服务连接中断。请双击同目录的「打开资金图表.cmd」重新连接。', true);
        } finally {
            polling = false;
            timer = setTimeout(poll, requested ? 0 : delay);
            requested = false;
        }
    }
    window.addEventListener('public-status-refresh', poll);
    poll();
})();
