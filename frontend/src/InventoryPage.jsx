import { createSignal, createEffect, For, Show } from "solid-js";
import { fetchInventory } from "./api";

const WIDTH_PRESETS = [1, 2, 4, 8, 12, 24];

const statusLabel = {
  pending: "待复核",
  processing: "复核中",
  done: "已完成",
};

const bucketMeta = {
  pass: { label: "合格", cls: "pass" },
  fail: { label: "超差", cls: "fail" },
  unsettled: { label: "未结清", cls: "unsettled" },
};

function fmtTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

export default function InventoryPage(props) {
  // 选窗信号只负责把参数交给服务端，本页不做任何加总或分桶。
  const [width, setWidth] = createSignal(1);
  const [endAt, setEndAt] = createSignal("");
  const [refreshTick, setRefreshTick] = createSignal(0);

  const [data, setData] = createSignal(null);
  const [loading, setLoading] = createSignal(false);
  const [error, setError] = createSignal("");

  // 调窗（窗宽/窗止）后当场重算：信号一变就重新请求服务端聚合。
  createEffect(() => {
    const w = width();
    const e = endAt();
    const tick = refreshTick();
    let cancelled = false;
    setLoading(true);
    setError("");
    fetchInventory({ widthHours: w, endAt: e })
      .then((payload) => {
        if (!cancelled) setData(payload);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message);
          setData(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  });

  const canWrite = () => !!props.user?.can_write;

  return (
    <div class="inventory">
      <Show when={error()}>
        <div class="banner error">{error()}</div>
      </Show>

      {/* 上：选窗。操作员可调；复核员只读，控件禁用。 */}
      <section class="card">
        <div class="toolbar">
          <h2>盘点台 · 小时窗超差盘点</h2>
          <Show when={data()}>
            <span class="hint">
              {loading() ? "重算中…" : "已按服务端口径重算"}
            </span>
          </Show>
        </div>
        <div class="window-picker">
          <label>
            窗宽（小时）
            <select
              value={width()}
              disabled={!canWrite()}
              onChange={(e) => setWidth(Number(e.currentTarget.value))}
            >
              <For each={WIDTH_PRESETS}>
                {(w) => <option value={w}>{w} 小时</option>}
              </For>
            </select>
          </label>
          <label>
            窗止时刻（留空取当前整点）
            <input
              type="datetime-local"
              value={endAt()}
              disabled={!canWrite()}
              onChange={(e) => setEndAt(e.currentTarget.value)}
            />
          </label>
          <Show when={data()}>
            <p class="hint window-range">
              当前窗：{fmtTime(data().window.start)} 起，至 {fmtTime(data().window.end)} 止
              （{data().window.width_hours} 小时，左闭右开）
            </p>
          </Show>
        </div>
        <Show when={!canWrite()}>
          <p class="hint">复核员账号只读：窗宽与窗止不可调整，仅可查看与刷新。</p>
        </Show>
      </section>

      {/* 中：四类量与占比，数字原样来自服务端 buckets，本页不自行加总。 */}
      <section class="card">
        <h2>四类量与占比</h2>
        <Show when={data()} fallback={<p class="hint">{loading() ? "加载中…" : "暂无数据"}</p>}>
          {(d) => (
            <div class="bucket-grid">
              <For each={d().buckets}>
                {(b) => (
                  <div class={`bucket bucket-${b.key}`}>
                    <div class="bucket-label">
                      {b.key === "total" ? "合计（分母）" : bucketMeta[b.key]?.label || b.key}
                    </div>
                    <div class="bucket-count">{b.count}</div>
                    <div class="bucket-ratio">
                      {b.key === "total" ? "基准行，占比不计入对账" : `占比 ${b.ratio_text}`}
                    </div>
                  </div>
                )}
              </For>
            </div>
          )}
        </Show>
      </section>

      {/* 下：刷新、口径总览、按窗重查明细、服务端对账。 */}
      <section class="card">
        <div class="toolbar">
          <h2>按窗重查与口径总览</h2>
          <button
            type="button"
            class="ghost"
            disabled={loading()}
            onClick={() => setRefreshTick((n) => n + 1)}
          >
            {loading() ? "刷新中…" : "刷新"}
          </button>
        </div>

        <Show when={data()}>
          {(d) => (
            <>
              <div class="criterion">
                <h3>口径总览</h3>
                <p class="hint">{d().criterion}</p>
                <p class="hint">
                  窗边界：[<strong>{fmtTime(d().window.start)}</strong>,
                  <strong>{fmtTime(d().window.end)}</strong>)；
                  创建时刻压线窗起计入，压线窗止不计入，前后端口径一致。
                </p>
              </div>

              <div class={`recon ${d().reconciliation.difference === 0 ? "ok" : "bad"}`}>
                <strong>
                  对账{d().reconciliation.difference === 0 ? "一致" : "存在误差"}：
                </strong>
                {d().reconciliation.statement}
                <span class="hint">
                  （逐桶差 合格 {d().reconciliation.bucket_diffs.pass}、
                  超差 {d().reconciliation.bucket_diffs.fail}、
                  未结清 {d().reconciliation.bucket_diffs.unsettled}；
                  合计差 {d().reconciliation.total_diff}）
                </span>
              </div>

              <h3>按窗重查明细（{d().rows.length} 行，桶归属由服务端标注）</h3>
              <table>
                <thead>
                  <tr>
                    <th>桶</th>
                    <th>刀具</th>
                    <th>刀补 µm</th>
                    <th>状态</th>
                    <th>结论</th>
                    <th>创建时刻</th>
                  </tr>
                </thead>
                <tbody>
                  <For each={d().rows}>
                    {(row) => (
                      <tr>
                        <td>
                          <span class={`tag tag-${row.bucket} ${bucketMeta[row.bucket]?.cls || ""}`}>
                            {bucketMeta[row.bucket]?.label || row.bucket}
                          </span>
                        </td>
                        <td>{row.tool_code}</td>
                        <td>{row.offset_um}</td>
                        <td>{statusLabel[row.status] || row.status}</td>
                        <td class={row.verdict === "合格" ? "pass" : row.verdict === "超差" ? "fail" : ""}>
                          {row.verdict || "—"}
                        </td>
                        <td>{fmtTime(row.created_at)}</td>
                      </tr>
                    )}
                  </For>
                </tbody>
              </table>
              <Show when={!d().rows.length}>
                <p class="hint">本窗内暂无记录（窗收窄到不含相关时刻时即为空）。</p>
              </Show>
            </>
          )}
        </Show>
      </section>
    </div>
  );
}
