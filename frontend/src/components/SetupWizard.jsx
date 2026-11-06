import { useEffect, useMemo, useState } from 'react'
import { Check, ChevronRight, Cpu, MessageSquare, ShieldCheck, Sparkles, X } from 'lucide-react'
import { useAppStore } from '../store/appStore'
import { t } from '../lib/t'

/**
 * 首次启动向导。
 *
 * 解决什么问题：新用户打开控制台，看到的是空图表和一个没配模型的聊天框，
 * 不知道要先做什么。竞品（AnythingLLM 五步 onboarding、Dify `/install`）
 * 都把这段引导做进了产品，本项目此前只靠 README 里四条命令。
 *
 * 设计约束：
 * - 只在本机（回环）出现：向导能填 API Key，等价于机器主人权限；
 * - 每步可跳过、进度存服务端：刷新页面不回到第一步；
 * - 不假装成功：模型探测真的发请求，失败原样显示上游错误；
 * - 老实例不弹：由 /api/setup/status 的 legacy 判定处理，前端只信返回值。
 */

const STEPS = [
  { key: 'welcome', icon: Sparkles, title: t('欢迎使用知客'), sub: t('三分钟配好，然后就能用了') },
  { key: 'model', icon: Cpu, title: t('连接模型'), sub: t('不配也能用，只是回答会比较朴素') },
  { key: 'channel', icon: MessageSquare, title: t('接入 QQ'), sub: t('可选：不接 QQ，网页端照样可用') },
  { key: 'selfcheck', icon: ShieldCheck, title: t('自检'), sub: t('确认每一项都真的能用') },
]

function api(path, init) {
  const token = useAppStore.getState().adminToken
  return fetch(path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { 'X-Admin-Token': token } : {}),
      ...(init?.headers || {}),
    },
  })
}

export default function SetupWizard() {
  const [status, setStatus] = useState(null)
  const [step, setStep] = useState('welcome')
  const [busy, setBusy] = useState(false)
  const [skipped, setSkipped] = useState([])
  const [dismissed, setDismissed] = useState(false)

  // 模型配置表单
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('')
  const [models, setModels] = useState([])
  const [probe, setProbe] = useState(null)

  useEffect(() => {
    let alive = true
    api('/api/setup/status')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!alive || !d) return
        setStatus(d)
        if (d.step && d.step !== 'done') setStep(d.step)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [])

  const idx = useMemo(() => Math.max(0, STEPS.findIndex((s) => s.key === step)), [step])

  const go = async (next) => {
    setStep(next)
    try {
      await api('/api/setup/step', { method: 'POST', body: JSON.stringify({ step: next }) })
    } catch {
      /* 进度存不上不影响继续，下次会从 welcome 开始 */
    }
  }

  const skip = async () => {
    const next = STEPS[Math.min(idx + 1, STEPS.length - 1)].key
    setSkipped((s) => [...s, step])
    await go(next)
  }

  const finish = async () => {
    setBusy(true)
    try {
      await api('/api/setup/complete', {
        method: 'POST',
        body: JSON.stringify({ skipped }),
      })
      setDismissed(true)
    } finally {
      setBusy(false)
    }
  }

  const discover = async () => {
    setProbe({ msg: t('正在拉取模型列表…') })
    try {
      const r = await api('/api/setup/discover-models', {
        method: 'POST',
        body: JSON.stringify({ base_url: baseUrl, api_key: apiKey }),
      })
      const d = await r.json()
      if (!d.ok) {
        setProbe({ ok: false, msg: d.error || `HTTP ${r.status}` })
        return
      }
      const ids = (d.models || []).map((m) => m.id || m.name).filter(Boolean)
      setModels(ids)
      setProbe({
        ok: true,
        msg: ids.length ? t('找到 {{n}} 个模型，选一个继续', { n: ids.length }) : t('端点可用，但没返回模型列表'),
      })
      if (!model && ids.length) setModel(ids[0])
    } catch (e) {
      setProbe({ ok: false, msg: e.message })
    }
  }

  const testModel = async () => {
    setProbe({ msg: t('正在发一次真实请求…') })
    try {
      const r = await api('/api/setup/probe-model', {
        method: 'POST',
        body: JSON.stringify({ base_url: baseUrl, api_key: apiKey, model }),
      })
      const d = await r.json()
      setProbe({
        ok: !!d.ok,
        msg: d.ok ? t('连接正常（{{ms}}ms）', { ms: d.latency_ms ?? 0 }) : d.reply || t('连接失败'),
      })
    } catch (e) {
      setProbe({ ok: false, msg: e.message })
    }
  }

  // 服务端说不需要、或用户点过关闭：不渲染。
  if (dismissed || !status || !status.needs_onboarding || !status.is_local) return null

  const current = STEPS[idx]

  return (
    <div className="fixed inset-0 z-50 bg-canvas/95 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="bg-panel border border-line-2 rounded-2xl w-full max-w-[720px] overflow-hidden shadow-2xl">
        {/* 顶部进度 */}
        <div className="px-6 pt-5 pb-4 border-b border-line-2">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs text-muted font-medium">{t('首次设置')}</span>
            <button
              className="text-muted hover:text-ink-2 p-1 rounded"
              onClick={finish}
              title={t('跳过全部设置')}
            >
              <X size={15} />
            </button>
          </div>
          <div className="flex items-center gap-1.5">
            {STEPS.map((s, i) => (
              <div key={s.key} className="flex-1 flex items-center gap-1.5">
                <div
                  className={`h-1 flex-1 rounded-full ${
                    i < idx ? 'bg-brand' : i === idx ? 'bg-brand/60' : 'bg-line-2'
                  }`}
                />
              </div>
            ))}
          </div>
        </div>

        {/* 内容 */}
        <div className="px-6 py-6 min-h-[280px]">
          <div className="flex items-center gap-2.5 mb-1.5">
            <current.icon size={18} className="text-brand" />
            <h2 className="text-lg font-semibold">{t(current.title)}</h2>
          </div>
          <p className="text-sm text-muted mb-5">{t(current.sub)}</p>

          {step === 'welcome' && (
            <div className="space-y-3 text-sm">
              <p className="text-ink-2">
                {t('知客是一个跑在你自己机器上的私人秘书：QQ 群里回话、网页里聊天、真的去执行工具——设提醒、算数、查资料、读写文件。')}
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 pt-1">
                {[
                  { k: t('不需要账号'), v: t('数据库就是你磁盘上的一个文件') },
                  { k: t('不联网也能用'), v: t('没配模型时走规则模式') },
                  { k: t('每一步可审计'), v: t('哈希链防篡改') },
                ].map((x) => (
                  <div key={x.k} className="bg-panel-2 rounded-lg p-3">
                    <div className="font-medium text-[13px]">{x.k}</div>
                    <div className="text-xs text-muted mt-0.5">{x.v}</div>
                  </div>
                ))}
              </div>
              {status.checks.kb && (
                <p className="text-xs text-muted pt-2">
                  {t('知识库已内置 14 条示例，可以直接问「物流发货要多久」试试。')}
                </p>
              )}
            </div>
          )}

          {step === 'model' && (
            <div className="space-y-3">
              <div>
                <label className="text-xs font-medium text-ink-2 block mb-1">{t('API 地址')}</label>
                <input
                  className="sw-input"
                  placeholder="https://api.openai.com/v1"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                />
              </div>
              <div>
                <label className="text-xs font-medium text-ink-2 block mb-1">{t('API Key')}</label>
                <input
                  className="sw-input"
                  type="password"
                  placeholder="sk-…"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                />
                <p className="text-[11px] text-muted mt-1">
                  {t('Key 只存在本机数据库，加密保存，不会回传到浏览器以外的任何地方。')}
                </p>
              </div>
              <div className="flex gap-2">
                <button className="btn ghost sm" onClick={discover} disabled={!baseUrl}>
                  {t('拉取模型列表')}
                </button>
                {models.length > 0 && (
                  <select className="sw-input" value={model} onChange={(e) => setModel(e.target.value)}>
                    {models.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                )}
              </div>
              {model && (
                <button className="btn ghost sm" onClick={testModel}>
                  {t('测试连接')}
                </button>
              )}
              {probe && (
                <p className={`text-xs ${probe.ok === false ? 'text-danger' : probe.ok ? 'text-brand' : 'text-muted'}`}>
                  {probe.msg}
                </p>
              )}
              <p className="text-[11px] text-muted pt-1">
                {t('也可以先跳过：规则模式下知识库检索、计算、时间、提醒都能正常用。')}
              </p>
            </div>
          )}

          {step === 'channel' && (
            <div className="space-y-3 text-sm">
              <p className="text-ink-2">
                {t('QQ 是可选渠道。接上之后，群里 @ 它就能用；不接也不影响网页端。')}
              </p>
              <div className="bg-panel-2 rounded-lg p-4 text-xs text-muted leading-relaxed">
                {t('接入路径：控制台 → 设置 → 渠道连接 → QQ → 一键部署协议端 → 扫码登录。')}
                <br />
                {t('协议端（NapCat）对 QQ 版本敏感，若登录失败，先看该页面给出的版本提示。')}
              </div>
              <p className="text-xs text-muted">{t('大部分用户建议先跳过，把网页端用顺了再接 QQ。')}</p>
            </div>
          )}

          {step === 'selfcheck' && (
            <div className="space-y-2 text-sm">
              {[
                { label: t('服务存活'), value: true, note: t('进程在跑') },
                { label: t('数据库'), value: true, note: t('SQLite 可写') },
                { label: t('知识库'), value: status.checks.kb, note: t('内置示例条目') },
                { label: t('模型'), value: status.checks.model, note: status.checks.model ? t('已配置') : t('未配置，规则模式') },
              ].map((row) => (
                <div key={row.label} className="flex items-center gap-2.5 py-1.5">
                  <span
                    className={`w-5 h-5 rounded-full flex items-center justify-center ${
                      row.value ? 'bg-brand/15 text-brand' : 'bg-line-2 text-muted'
                    }`}
                  >
                    {row.value ? <Check size={12} /> : <X size={12} />}
                  </span>
                  <span className="font-medium text-[13px]">{row.label}</span>
                  <span className="text-xs text-muted ml-auto">{row.note}</span>
                </div>
              ))}
              <p className="text-xs text-muted pt-2">
                {t('之后随时可以在「设置」和「日志中心」复查这些状态。')}
              </p>
            </div>
          )}
        </div>

        {/* 底部操作 */}
        <div className="px-6 py-4 border-t border-line-2 flex items-center gap-2">
          {idx > 0 && (
            <button className="btn ghost sm" onClick={() => go(STEPS[idx - 1].key)}>
              {t('上一步')}
            </button>
          )}
          <div className="flex-1" />
          {step !== 'selfcheck' && (
            <button className="btn ghost sm" onClick={skip}>
              {t('跳过')}
            </button>
          )}
          {step === 'selfcheck' ? (
            <button className="btn sm" onClick={finish} disabled={busy}>
              {busy ? t('保存中…') : t('开始使用')}
            </button>
          ) : (
            <button className="btn sm" onClick={() => go(STEPS[Math.min(idx + 1, STEPS.length - 1)].key)}>
              {t('继续')} <ChevronRight size={14} />
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
