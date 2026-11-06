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
 * - 二维码固定 10 秒自动换新，任何时刻看到的码都新鲜（NapCat 的码约 2 分钟过期）；
 * - 顶部有倒计时，用户知道还有多久换一次，不会以为是卡住了；
 * - 3 秒轮询登录状态，扫码成功后立刻切到成功态，不需要手动刷新；
 * - 失败时把 NapCat 的真实错误原样显示，不假装「正在加载」。
 */
const REFRESH_S = 10
const POLL_MS = 3000

type QrResp = { configured?: boolean; qrcode_url?: string; error?: string }
type LoginStatus = {
  configured?: boolean
  reachable?: boolean
  is_login?: boolean
  is_offline?: boolean
  login_phase?: number | null
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
  const aliveRef = useRef(true)

  const loadQr = useCallback(async (force: boolean) => {
    try {
      const r = await getJson<QrResp>(`/api/channel/qq/qr${force ? '?refresh=1' : ''}`)
      if (!aliveRef.current) return
      setQr(r)
      setSeconds(0)
    } catch (e) {
      if (aliveRef.current) setQr({ error: (e as Error).message })
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

  const loggedIn = !!status?.is_login
  const remaining = Math.max(0, REFRESH_S - seconds)

  return (
    <div className="p-4 md:p-6 max-w-[720px] mx-auto w-full min-w-0 space-y-5 pb-24 md:pb-10">
      <PageHead
        title={t('QQ 扫码登录')}
        sub={t('二维码每 10 秒自动换新；用手机 QQ 扫一次即可，登录成功后本页会自动更新')}
        right={null}
      />

      <div className="bg-panel border border-line-2 rounded-2xl p-5 sm:p-8 flex flex-col items-center gap-5 min-w-0">
        {loggedIn ? (
          <div className="flex flex-col items-center gap-3 py-10">
            <div className="w-16 h-16 rounded-full bg-brand/10 flex items-center justify-center">
              <CheckCircle2 size={34} className="text-brand" />
            </div>
            <p className="text-base font-semibold">{t('已登录')}</p>
            <p className="text-sm text-muted text-center max-w-[36ch]">
              {t('QQ 渠道已就绪。回到控制台即可在群里 @ 它或私聊使用。')}
            </p>
          </div>
        ) : (
          <>
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
                    ? t('{{n}} 秒后自动换新码', { n: remaining })
                    : t('正在换新码…')}
                </span>
              </div>
            )}

            {qr?.error && (
              <div className="text-sm text-danger text-center max-w-[52ch] leading-relaxed">{qr.error}</div>
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
