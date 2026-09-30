import { createSignal, createEffect, Show, For, onMount } from "solid-js";
import { fetchWindowInventory } from "./api";

const statusLabel = {
  pending: "待复核",
  processing: "复核中",
  done: "已完成",
};

const QUICK_HOURS = [1, 2, 4, 8, 24];

function pad2(n) {
  return String(n).padStart(2, "0");
}

// datetime-local 控件值（浏览器按本地时区解释）→ ISO8601 UTC，窗边界时刻不在转换中走样。
function localInputToIso(value) {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  return d.toISOString();
}

// ISO → datetime-local 本地时间串
function isoToLocalInput(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(
    d.getHours()
  )}:${pad2(d.getMinutes())}`;
}

function formatTime(iso) {
  return iso ? new Date(iso).toLocaleString() : "—";
}

function formatRatio(r) {
  // 只做服务端占比的展示换算（0.xx → xx.x%），不参与任何计数。
  return r === null || r === undefined ? "—" : `${(r * 100).toFixed(1)}%`;
}

export default function InventoryPage(props) {
  const canWrite = () => !!props.user?.can_write;

  const [mode, setMode] = createSignal("rolling");
  const [hours, setHours] = createSignal("1");
  const [startStr, setStartStr] = createSignal("");
  const [endStr, setEndStr] = createSignal("");
  // 已生效的窗参数：只有它变化才重查；改草稿不会触发请求。
  const [applied, setApplied] = createSignal({ hours: 1 });
  const [refreshNonce, setRefreshNonce] = createSignal(0);

  const [data, setData] = createSignal(null);
  const [loading, setLoading] = createSignal(false);
  const [error, setError] = createSignal("");

  onMount(() => {
    const end = new Date();
    const start = new Date(end.getTime() - 60 * 60 * 1000);
    setStartStr(isoToLocalInput(start.toISOString()).slice(0, 16));
    setEndStr(isoToLocalInput(end.toISOString()).slice(0, 16));
  });

  function applyRolling(h) {
    const n = Number(h);
    if (!Number.isFinite(n) || n <= 0) {
      setError("窗宽必须是大于 0 的小时数");
      return;
    }
    setError("");
    setMode("rolling");
    setHours(String(h));
    setApplied({ hours: n });
  }

  function applyRange() {
    const start = localInputToIso(startStr());
    const end = localInputToIso(endStr());
    if (!start || !end) {
      setError("请先选完整的窗起止时刻");
      return;
    }
    if (new Date(end) <= new Date(start)) {
      setError("窗的结束时刻必须晚于开始时刻");
      return;
    }
    setError("");
    setMode("range");
    setApplied({ start, end });
  }

  async function load() {
    setLoading(true);
    setError("");
    try {
      // 整页数字（四类量、占比、明细）全部来自这一次服务端响应，前端只展示。
      const payload = await fetchWindowInventory(applied());
      setData(payload);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  createEffect(() => {
    // 依赖 applied() 与 refreshNonce()：调窗后当场重算，刷新按钮按同一窗重查。
    applied();
    refreshNonce();
    load();
  });

  const counts = () => data()?.counts ?? null;
  const ratios = () => data()?.ratios ?? null;
  const items = () => data()?.items ?? [];

  return (
    <section class="inventory-page">
      <div class="card">
        <h2>超差盘点台 · 按小时窗整页盘点</h2>
        <div class="window-picker">
          <div class="window-mode">
            <label class="inline-field">
              <input
                type="radio"
                name="win-mode"
                value="rolling"
                checked={mode() === "rolling"}
                disabled={!canWrite()}
                onChange={() => applyRolling(hours())}
              />
              最近窗宽
            </label>
            <label class="inline-field">
              <input
                type="number"
                min="0.25"
                step="0.25"
                value={hours()}
                disabled={!canWrite() || mode() !== "rolling"}
                onInput={(e) => setHours(e.currentTarget.value)}
                onKeyDown={(e) => e.key === "Enter" && applyRolling(hours())}
              />
              <span>小时</span>
            </label>
            <For each={QUICK_HOURS}>
              {(h) => (
                <button
                  type="button"
                  class="ghost"
                  disabled={!canWrite()}
                  onClick={() => applyRolling(h)}
                >
                  {h}h
                </button>
              )}
            </For>
          </div>
          <div class="window-mode">
            <label class="inline-field">
              <input
                type="radio"
                name="win-mode"
                value="range"
                checked={mode() === "range"}
                disabled={!canWrite()}
                onChange={applyRange}
              />
              自定义窗
            </label>
            <input
              type="datetime-local"
              value={startStr()}
              disabled={!canWrite()}
              onInput={(e) => setStartStr(e.currentTarget.value)}
            />
            <span>至</span>
            <input
              type="datetime-local"
              value={endStr()}
              disabled={!canWrite()}
              onInput={(e) => setEndStr(e.currentTarget.value)}
            />
            <button type="button" class="ghost" disabled={!canWrite()} onClick={applyRange}>
              应用窗
            </button>
          </div>
          <Show when={!canWrite()}>
            <p class="hint">复核员账号只读：窗宽不可调，仅可按当前窗刷新复核。</p>
          </Show>
        </div>
      </div>

      <Show when={error()}>
        <div class="banner error">{error()}</div>
      </Show>

      <Show when={data()}>
        <div class="stat-grid">
          <div class="card stat pass-stat">
            <div class="stat-label">合格量</div>
            <div class="stat-value">{counts().pass}</div>
            <div class="stat-ratio">占比 {formatRatio(ratios().pass)}</div>
          </div>
          <div class="card stat fail-stat">
            <div class="stat-label">超差量</div>
            <div class="stat-value">{counts().fail}</div>
            <div class="stat-ratio">占比 {formatRatio(ratios().fail)}</div>
          </div>
          <div class="card stat">
            <div class="stat-label">未结清量</div>
            <div class="stat-value">{counts().unsettled}</div>
            <div class="stat-ratio sub">不参与占比</div>
          </div>
          <div class="card stat">
            <div class="stat-label">窗内总量</div>
            <div class="stat-value">{counts().total}</div>
            <div class="stat-ratio sub">已结清 {counts().settled_total} 笔</div>
          </div>
        </div>

        <div class="card toolbar">
          <button type="button" class="ghost" onClick={() => setRefreshNonce((n) => n + 1)} disabled={loading()}>
            {loading() ? "刷新中…" : "刷新"}
          </button>
          {/* 口径总览一行：只陈述边界与分母口径，不放任何百分比数字 */}
          <p class="calibration">
            口径总览：窗 [{formatTime(data().window.start)} ~ {formatTime(data().window.end)})
            半开区间（{data().window.boundary_rule}）；{data().window.ratio_basis}。
          </p>
        </div>

        <div class="card">
          <h3>按窗重查明细</h3>
          <table>
            <thead>
              <tr>
                <th>编号</th>
                <th>刀具</th>
                <th>刀补 µm</th>
                <th>状态</th>
                <th>结论</th>
                <th>归属桶</th>
                <th>创建时刻</th>
                <th>复核时刻</th>
              </tr>
            </thead>
            <tbody>
              <For each={items()}>
                {(row) => (
                  <tr>
                    <td>{row.id}</td>
                    <td>{row.tool_code}</td>
                    <td>{row.offset_um}</td>
                    <td>{statusLabel[row.status] || row.status}</td>
                    <td class={row.verdict === "合格" ? "pass" : row.verdict === "超差" ? "fail" : ""}>
                      {row.verdict || "—"}
                    </td>
                    <td>
                      <span class={`bucket bucket-${row.bucket}`}>
                        {row.bucket === "pass" ? "合格" : row.bucket === "fail" ? "超差" : "未结清"}
                      </span>
                    </td>
                    <td>{formatTime(row.created_at)}</td>
                    <td>{formatTime(row.reviewed_at)}</td>
                  </tr>
                )}
              </For>
            </tbody>
          </table>
          <Show when={!items().length && !loading()}>
            <p class="hint">本窗内暂无记录</p>
          </Show>
        </div>
      </Show>
    </section>
  );
}
