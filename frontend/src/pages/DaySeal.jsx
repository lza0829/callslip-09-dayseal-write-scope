import { useEffect, useState } from 'react'
import { api } from '../api'
import { GateChips } from '../components/GateBanner'

export default function DaySeal() {
  const [info, setInfo] = useState(null)
  const [events, setEvents] = useState([])
  const [day, setDay] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  // 封口事件、写闸、日切页同刷：任何操作后整体重载
  const load = () => Promise.all([api.sealInfo(), api.sealEvents()]).then(([d, ev]) => {
    setInfo(d)
    setEvents(ev.事件 || [])
    setDay(d.营业日 || '')
  }).catch(e => setErr(e.message))
  useEffect(() => { load() }, [])
  const run = async (fn, ok) => {
    setErr(''); setMsg('')
    try { await fn(); setMsg(ok) } catch (e) { setErr(e.message) }
    load()
  }
  if (!info && !err) return <p className="muted">加载日切…</p>
  return (
    <div className="page">
      <header className="hero">
        <p className="eyebrow">当日写闸</p>
        <h1>日切封口</h1>
        <p className="lede">
          全日号牌写闸：封口后当日一切号牌写（发号、叫号、过号、办结与号段游标推进）一律拒绝，
          总览与列表只读可查；臂内解封后写闸重开。须封口后再切营业日。
        </p>
      </header>
      {info && (
        <section className="panel">
          <div className="stat-grid">
            <div className="stat"><span>营业日</span><strong style={{ fontSize: '1.2rem' }}>{info.营业日}</strong></div>
            <div className="stat"><span>封口</span><strong>{info.已封口 ? '是' : '否'}</strong></div>
            <div className="stat"><span>未结号</span><strong>{info.未结号数}</strong></div>
          </div>
          <GateChips gate={info.写闸} />
          <p className="muted">{info.备注}{info.封口时刻 ? ` · ${info.封口时刻}` : ''}</p>
          <div className="actions">
            <button type="button" onClick={() => run(() => api.seal(false), '已封口')} disabled={info.已封口}>封口</button>
            <button type="button" className="ghost" onClick={() => run(() => api.seal(true), '已强制封口')} disabled={info.已封口}>强制封口</button>
            <button type="button" className="ghost" onClick={() => run(() => api.unseal(), '已解封')} disabled={!info.已封口}>解封</button>
          </div>
        </section>
      )}
      <section className="panel">
        <h2>封口事件流水（写闸判定由此重放）</h2>
        <ul className="plain">
          {events.length === 0 && <li className="muted">当日尚无封口事件，写闸为开</li>}
          {events.map(e => (
            <li key={e.id}>#{e.id} · {e.时刻} · {e.动作} · {e.备注}</li>
          ))}
        </ul>
      </section>
      <section className="panel">
        <h2>切日</h2>
        <div className="form row">
          <label>新营业日
            <input value={day} onChange={e => setDay(e.target.value)} placeholder="年-月-日" />
          </label>
          <button type="button" onClick={() => run(() => api.roll(day || null), '已切日')}>切到新营业日</button>
        </div>
        {err && <p className="err">{err}</p>}
        {msg && <p className="muted">{msg}</p>}
      </section>
    </div>
  )
}
