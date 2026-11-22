/* The data bundles contain dated observations. All date comparisons are inclusive. */
(() => {
    "use strict";
    const series = window.SERIES_DATA;
    const flow = window.FLOW_DATA;
    if (!series || !flow) return;

    const market = window.MARKET_FLOW;
    const marketDates = market?.dates || [];
    const marketIndex = new Map(marketDates.map((day, i) => [day, i]));
    const marketStart = marketDates.find((_, i) => Number.isFinite(market.observed[i])) || null;
    const marketEnd = marketDates.findLast((_, i) => Number.isFinite(market.observed[i])) || null;
    const calendar = Array.from(new Set([...series.dates, ...flow.dates, ...marketDates])).sort();
    const seriesIndex = new Map(series.dates.map((day, index) => [day, index]));
    const yields = window.PUBLIC_YIELDS || {};
    const yieldDates = yields.dates || [];
    const yieldIndex = new Map(yieldDates.map((day, i) => [day, i]));
    const yieldCalendar = Array.from(new Set([...calendar, ...yieldDates])).sort();
    const positive = value => Number.isFinite(value) && value > 0;
    const numberOrNull = value => Number.isFinite(value) ? value : null;
    const at = (values, day) => numberOrNull(values?.[seriesIndex.get(day)]);

    function position(index, start, end, mode, clippedStart) {
        let last = end;
        const floor = mode === "range" ? start : 0;
        while (last >= floor && !positive(at(index.values, calendar[last]))) last--;
        const result = {name: index.name, code: index.code, date: null, first: null,
            n: 0, expected: 0, close: null, pct: null, drawdown: null};
        if (last < floor) return result;
        const first = mode === "range" ? start : Math.max(0, last - 249);
        const values = calendar.slice(first, last + 1).map(day => at(index.values, day));
        const observed = values.filter(positive);
        const close = values[values.length - 1];
        Object.assign(result, {date: calendar[last], first: calendar[first],
            n: observed.length, expected: mode === "range" ? values.length : 250, close});
        if (observed.length === result.expected && !(mode === "range" && clippedStart)) {
            result.pct = 100 * observed.filter(value => value <= close).length / result.expected;
            result.drawdown = 100 * (close / Math.max(...observed) - 1);
        }
        return result;
    }

    function snapshot(end) {
        const endIndex = flow.dates.findLastIndex(day => day <= end);
        return flow.groups.map((name, column) => {
            for (let i = endIndex; i >= 0; i--) {
                const value = flow.daily[i][column];
                if (Number.isFinite(value)) return {name, date: flow.dates[i], value};
            }
            return {name, date: null, value: null};
        });
    }

    function windowFlows(lo, hi, requestStart, base, scope) {
        const history = base === "history";
        const strictScope = scope === "strict";
        const baseDate = history ? marketStart : calendar[lo];
        const startIndex = history && marketStart ? calendar.indexOf(marketStart) : lo;
        const leftTruncated = !history && requestStart < calendar[0];
        const universeNote = market?.universeNote || "按公开基金目录与上市、退市日期确定当日可识别的宽基 ETF。";
        const result = {
            daily: [], cumulative: [], observed: [], strict: [], covered: [], eligible: [],
            cumulativePartial: [], baseDate, base, scope, marketStart, marketEnd,
            completeDays: 0, partialDays: 0, missingDays: 0,
            baseCompleteDays: 0, basePartialDays: 0, baseMissingDays: 0, leftTruncated,
            observedSubtotal: null, lastObservedDate: null,
            source: market?.source || "动态名单数据未载入",
            universeNote
        };
        let sum = 0;
        let incomplete = leftTruncated;
        for (let i = Math.min(lo, startIndex); i <= hi; i++) {
            const j = marketIndex.get(calendar[i]);
            const observed = numberOrNull(market?.observed[j]);
            const strict = numberOrNull(market?.daily[j]);
            const bucket = strict !== null ? "CompleteDays" : observed !== null ? "PartialDays" : "MissingDays";
            const value = strictScope ? strict : observed;
            let running = null;
            if (i >= startIndex && baseDate) {
                result["base" + bucket]++;
                if (strict === null) incomplete = true;
                if (observed !== null) {
                    result.observedSubtotal = (result.observedSubtotal ?? 0) + observed;
                    result.lastObservedDate = calendar[i];
                }
                if (value !== null) {
                    sum += value;
                    if (!strictScope || !incomplete) running = sum;
                }
            }
            if (i < lo) continue;
            const key = bucket[0].toLowerCase() + bucket.slice(1);
            result[key]++;
            result.observed.push(observed);
            result.strict.push(strict);
            result.covered.push(numberOrNull(market?.covered[j]));
            result.eligible.push(numberOrNull(market?.eligible[j]));
            result.daily.push(value);
            result.cumulative.push(running);
            result.cumulativePartial.push(incomplete);
        }
        return result;
    }

    function isoDate(value) {
        if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
        const parsed = new Date(value + "T00:00:00Z");
        return Number.isFinite(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
    }

    function yieldSeries(dates) {
        const values = field => dates.map(day => yields[field]?.[yieldIndex.get(day)] ?? null);
        const dividend = values("dividend"), bond = values("bond");
        const spread = dividend.map((v, i) => Number.isFinite(v) && Number.isFinite(bond[i]) ? v - bond[i] : null);
        const result = {dates, dividend, bond, spread};
        for (const field of ["dividendCount", "dividendCoverage", "bondCode", "bondBanks"]) {
            if (yields[field]?.some(v => v != null)) result[field] = values(field);
        }
        return result;
    }
    const fullYields = yieldSeries(yieldDates);
    const yieldHistory = {first: yieldDates.find((d, i) =>
        Number.isFinite(fullYields.dividend[i]) || Number.isFinite(fullYields.bond[i])) || null, latest: {}};
    for (const field of ["dividend", "bond", "spread"]) {
        const i = fullYields[field].findLastIndex(Number.isFinite);
        yieldHistory.latest[field] = i < 0 ? null : {date: yieldDates[i], value: fullYields[field][i]};
    }

    function calculate(requestStart, requestEnd, mode = "250", flowBase = "selection", flowScope = "observed", chartId) {
        if (!isoDate(requestStart) || !isoDate(requestEnd)) return {error: "请选择有效的开始日期和结束日期。"};
        if (requestStart > requestEnd) return {error: "开始日期晚于结束日期，请调整所选区间。"};
        const yieldDays = yieldCalendar;
        const sessions = chartId === 6 ? yieldDays : calendar;
        const minimum = sessions[0], maximum = sessions[sessions.length - 1];
        if (!minimum) return {error: "尚无可用交易数据。"};
        if (requestEnd < minimum || requestStart > maximum) {
            return {error: "所选区间超出本地数据覆盖范围（" + minimum + " 至 " + maximum + "）。"};
        }
        const lo = sessions.findIndex(day => day >= requestStart);
        const hi = sessions.findLastIndex(day => day <= requestEnd);
        if (lo > hi) return {error: chartId === 6 ? "所选区间内没有 A 股交易日或银行间市场观测日。" :
            "所选区间内没有 A 股交易日，请选择包含交易日的区间。"};
        const dates = sessions.slice(lo, hi + 1);
        const [start, end] = [sessions[lo], sessions[hi]];
        const clippedStart = requestStart < minimum;
        const warnings = [];
        if (clippedStart || requestEnd > maximum) {
            warnings.push("所选区间部分超出" + (chartId === 6 ? "本图" : "A 股") + "日历范围（" + minimum + " 至 " + maximum + "）。");
        }
        if (clippedStart && chartId === undefined) warnings.push("区间起点缺少历史，完整区间累计和自选窗口分位保留缺失。");
        const result = {start, end, requestedStart: requestStart, requestedEnd: requestEnd, dates,
            sources: {}, warning: warnings.join(" ")};
        const select = values => dates.map(day => at(values, day));
        const all = chartId === undefined;
        if (all || chartId === 3) {
            result.flows = windowFlows(lo, hi, requestStart, flowBase, flowScope);
            result.sources.flow = result.flows.source;
        }
        if (all || chartId === 4) {
            result.shares = {fund: select(series.shares), index: select(series.hs300)};
            result.sources.shares = series.sources.shares;
        }
        if (all || chartId === 5) {
            result.margin = {sh: select(series.marginSH), sz: select(series.marginSZ)};
            result.sources.margin = series.sources.margin;
        }
        if (all || chartId === 6) {
            const yieldDates = all ? yieldDays.filter(day => day >= requestStart && day <= requestEnd) : dates;
            result.yields = yieldSeries(yieldDates);
            result.sources.yields = (yields.source || "来源见本地股债数据包");
            result.yields.warning = requestStart < yieldDays[0] || requestEnd > yieldDays.at(-1) ?
                "本图日历覆盖 " + yieldDays[0] + " 至 " + yieldDays.at(-1) + "。" : "";
        }
        if (all) {
            result.positions = series.indices.map(index => position(index, lo, hi, mode, clippedStart));
            result.snapshot = snapshot(end);
            result.poolSize = flow.poolSize;
            result.sources = {...series.sources, flow: flow.source + "。固定样本基准日 " + flow.poolAsOf + "。", yields: (yields.source || "来源见本地股债数据包")};
        }
        return result;
    }
    const historyStartByChart = Object.freeze({
        3: marketStart,
        4: series.dates.find((_, i) => Number.isFinite(series.shares?.[i]) || Number.isFinite(series.hs300?.[i])) || null,
        5: series.dates.find((_, i) => Number.isFinite(series.marginSH?.[i]) || Number.isFinite(series.marginSZ?.[i])) || null,
        6: yieldHistory.first
    });
    window.Monitor = Object.freeze({calendar: Object.freeze(calendar), marketStart, marketEnd, historyStartByChart, yieldHistory, calculate});
})();
