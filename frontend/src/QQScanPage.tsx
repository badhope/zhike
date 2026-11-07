import { useCallback, useEffect, useRef, useState } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { CheckCircle2, Loader2, RefreshCw, Smartphone } from 'lucide-react'
import { PageHead } from './ui'
import { t } from './lib/t'

/**
 * QQ 扫码登录 · 独立常驻页。
 *
 * 为什么单独做一页，而不是复用「接入 QQ」弹窗：
 * 用户的实际用法是「把二维码摆在旁边，拿手机扫」。弹窗会挡住控制台、
 * 误点遮罩就关闭、也不方便并排放着别的窗口。这一页设计成可以长期开着：
 *
 * - 二维码保持有效，只在临近过期时自动换新；
 * - 顶部有倒计时，用户知道码还能用多久，不会以为是卡住了；
 * - 3 秒轮询登录状态，扫码成功后立刻切到成功态，不需要手动刷新；
 * - 失败时把 NapCat 的真实错误原样显示，不假装「正在加载」。
 *
 * ## 为什么不是每 10 秒换码（重要）
 *
 * 最初的需求是「十秒刷新一次，防止失效」。但必须区分两件事：
 *
 *   1. **轮询登录状态**：每 10 秒问一次，无副作用、成本极低 —— 保留。
 *   2. **换码 RefreshQRcode**：会让服务端**作废旧码**。若按 10 秒做，
 *      用户掏出手机 → 对准 → 确认通常要 3-8 秒，很容易正好扫到刚被作废的
 *      那一张，表现是「扫了没反应」，且极难归因（看起来像网络问题）。
 *
 * 实测：不主动 refresh 时，服务端连续三次返回同一个码（有效期内复用），
 * 真正的过期由 NapCat 按约 2 分钟判断。所以正确策略是「让码活着、快过期
 * 再换」，而不是「频繁换新」。用户要的「防止失效」这样才真正满足。
 */
const REFRESH_S = 100 // 距过期约 100 秒时主动换新（NapCat 约 120 秒过期）
const POLL_MS = 3000

type QrResp = { configured?: boolean; qrcode_url?: string; error?: string }
type Account = {
  uin: string
  nick: string
  avatar_url: string
  logged_in_at: string
  is_current: boolean
}
type LoginStatus = {
  configured?: boolean
  reachable?: boolean
  is_login?: boolean
  is_offline?: boolean
  login_phase?: number | null
  /** 服务端认定的当前账号（实时登录结果优先，其次是上次记录） */
  account?: Account
  error?: string
}

function adminHeaders(): Record<string, string> {
  const token = localStorage.getItem('cs-agent-app')
  let t = ''
  try {
    t = JSON.parse(token || '{}')?.state?.adminToken || ''
  } catch {
    t = ''
  }
  return t ? { 'X-Admin-Token': t } : {}
}

async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url, { headers: adminHeaders() })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return (await r.json()) as T
}

export default function QQScanPage() {
  const [qr, setQr] = useState<QrResp | null>(null)
  const [status, setStatus] = useState<LoginStatus | null>(null)
  const [seconds, setSeconds] = useState(0)
  const [loading, setLoading] = useState(true)
  const [retryIn, setRetryIn] = useState(0)
  const aliveRef = useRef(true)
  const failRef = useRef(0)

  const loadQr = useCallback(async (force: boolean) => {
    try {
      const r = await getJson<QrResp>(`/api/channel/qq/qr${force ? '?refresh=1' : ''}`)
      if (!aliveRef.current) return
      setQr(r)
      setSeconds(0)
      // 拿到了码就清零失败计数，避免下次故障时直接从长退避开始
      if (r.qrcode_url) failRef.current = 0
    } catch (e) {
      if (!aliveRef.current) return
      failRef.current += 1
      setQr({ error: (e as Error).message })
    } finally {
      if (aliveRef.current) setLoading(false)
    }
  }, [])

  const loadStatus = useCallback(async () => {
    try {
      const s = await getJson<LoginStatus>('/api/channel/qq/login-status')
      if (aliveRef.current) setStatus(s)
    } catch {
      /* 下一轮再试 */
    }
  }, [])

  useEffect(() => {
    aliveRef.current = true
    void loadQr(false)
    void loadStatus()

    const tick = setInterval(() => setSeconds((s) => s + 1), 1000)
    const poll = setInterval(() => void loadStatus(), POLL_MS)
    return () => {
      aliveRef.current = false
      clearInterval(tick)
      clearInterval(poll)
    }
  }, [loadQr, loadStatus])

  // 到点换新码；已登录就不再请求，避免无意义刷新
  useEffect(() => {
    if (status?.is_login) return
    if (!qr?.qrcode_url) return
    if (seconds < REFRESH_S) return
    void loadQr(true)
  }, [seconds, qr?.qrcode_url, status?.is_login, loadQr])

  // 拿码失败时自动重试：退避 5s → 10s → 20s → 40s → 60s 封顶。
  // 之前失败后只显示一行错误就不动了，用户必须手动点「刷新状态」——
  // 典型场景是协议端刚启动、WebUI 还没 ready，等十几秒自然就好了，
  // 不该让人盯着屏幕等。成功后 failRef 归零，恢复正常节奏。
  useEffect(() => {
    if (qr?.qrcode_url || status?.is_login) return
    if (!qr?.error) return
    const delay = Math.min(5 * 2 ** Math.max(0, failRef.current - 1), 60)
    setRetryIn(delay)
    let left = delay
    const tick = setInterval(() => {
      left -= 1
      setRetryIn(left)
      if (left <= 0) {
        clearInterval(tick)
        void loadQr(false)
      }
    }, 1000)
    return () => clearInterval(tick)
  }, [qr?.error, qr?.qrcode_url, status?.is_login, loadQr])

  // 扫码成功：自动清空二维码状态并给一条明确反馈。
  // 原来成功后只是把内容换成「已登录」，用户还得自己切页面；
  // 这里补一个自动跳转计时，同时保留手动按钮。
  useEffect(() => {
    if (!status?.is_login) return
    const t = setTimeout(() => {
      if (aliveRef.current) window.location.assign('/dashboard')
    }, 2500)
    return () => clearTimeout(t)
  }, [status?.is_login])

  const loggedIn = !!status?.is_login
  const remaining = Math.max(0, REFRESH_S - seconds)

  return (
    <div className="p-4 md:p-6 max-w-[720px] mx-auto w-full min-w-0 space-y-5 pb-24 md:pb-10">
      <PageHead
        title={t('QQ 扫码登录')}
        sub={t('二维码保持有效，临近过期才换新；用手机 QQ 扫一次即可，登录成功后本页会自动更新')}
        right={null}
      />

      <div className="bg-panel border border-line-2 rounded-2xl p-5 sm:p-8 flex flex-col items-center gap-5 min-w-0">
        {loggedIn ? (
          <div className="flex flex-col items-center gap-3 py-10">
            {status?.account?.avatar_url ? (
              <img
                src={status.account.avatar_url}
                alt=""
                width={72}
                height={72}
                style={{ borderRadius: '50%' }}
              />
            ) : (
              <div className="w-16 h-16 rounded-full bg-brand/10 flex items-center justify-center">
                <CheckCircle2 size={34} className="text-brand" />
              </div>
            )}
            <p className="text-base font-semibold">
              {status?.account?.nick || status?.account?.uin
                ? `${status.account.nick || ''}${status.account.uin ? ` (${status.account.uin})` : ''}`
                : t('已登录')}
            </p>
            <p className="text-sm text-muted text-center max-w-[36ch]">
              {t('QQ 渠道已就绪。回到控制台即可在群里 @ 它或私聊使用。')}
            </p>
            <p className="text-xs text-muted text-center max-w-[40ch]">
              {t('账号资料来自这次实际登录的结果；换号后这里会自动更新，不需要手动改配置。')}
            </p>
          </div>
        ) : (
          <>
            {/* 未登录但有历史账号时，明确标成"上次登录"，不能让人误以为还是当前账号 */}
            {status?.account?.uin && (
              <div className="flex items-center gap-2 text-xs text-muted">
                {status.account.avatar_url && (
                  <img
                    src={status.account.avatar_url}
                    alt=""
                    width={20}
                    height={20}
                    style={{ borderRadius: '50%' }}
                  />
                )}
                <span>
                  {t('上次登录：')}
                  {status.account.nick || status.account.uin}
                  {status.account.uin ? ` (${status.account.uin})` : ''}
                  {t(' · 扫码后将以新账号为准')}
                </span>
              </div>
            )}
            <div className="relative w-full max-w-[300px] flex justify-center">
              <div className="bg-white p-3 sm:p-4 rounded-xl border border-line-2 shadow-sm">
                {qr?.qrcode_url ? (
                  <QRCodeSVG
                    value={qr.qrcode_url}
                    size={232}
                    level="M"
                    style={{ width: '100%', height: 'auto', maxWidth: 232, display: 'block' }}
                  />
                ) : (
                  <div className="w-[232px] h-[232px] flex items-center justify-center text-muted text-sm">
                    {loading ? (
                      <span className="flex items-center gap-2">
                        <Loader2 size={16} className="animate-spin" /> {t('正在获取二维码…')}
                      </span>
                    ) : (
                      t('二维码不可用')
                    )}
                  </div>
                )}
              </div>
            </div>

            {qr?.qrcode_url && (
              <div className="flex items-center gap-2 text-sm text-muted">
                <RefreshCw size={13} className={remaining <= 3 ? 'animate-spin text-brand' : ''} />
                <span>
                  {remaining > 0
                    ? t('二维码有效 · {{n}} 秒后自动换新', { n: remaining })
                    : t('正在换新码…')}
                </span>
              </div>
            )}

            {qr?.error && (
              <div className="text-center max-w-[52ch] space-y-1.5">
                <div className="text-sm text-danger leading-relaxed">{qr.error}</div>
                {retryIn > 0 && (
                  <div className="text-xs text-muted">
                    {t('{{n}} 秒后自动重试（协议端刚启动时通常等十几秒即可）', { n: retryIn })}
                  </div>
                )}
              </div>
            )}

            <div className="flex items-start gap-2 text-xs text-muted max-w-[46ch] leading-relaxed">
              <Smartphone size={14} className="mt-0.5 shrink-0" />
              <span>
                {t('手机 QQ → 右上角「+」→ 扫一扫。扫码后手机上点确认，本页会自动跳到「已登录」。')}
              </span>
            </div>

            {status && !status.reachable && status.error && (
              <div className="text-xs text-warn text-center max-w-[52ch]">
                {t('协议端未就绪：')}
                {status.error}
              </div>
            )}
          </>
        )}
      </div>

      <div className="flex justify-center gap-2">
        <button className="btn ghost sm" onClick={() => void loadQr(true)} disabled={loggedIn}>
          <RefreshCw size={13} /> {t('立即换新码')}
        </button>
        <button className="btn ghost sm" onClick={() => void loadStatus()}>
          {t('刷新状态')}
        </button>
      </div>
    </div>
  )
}
