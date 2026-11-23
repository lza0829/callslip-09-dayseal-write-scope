import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'

// 全日号牌写闸：闸态唯一来源是后端 /日切（由封口事件重放得出）。
// 各写口页面只读这里的闸态，不各自判断，保证封口事件、闸状态、
// 写口、总览同刷一致。
export function useSealGate() {
  const [seal, setSeal] = useState(null)
  const reload = useCallback(() => {
    api.sealInfo().then(setSeal).catch(() => {})
  }, [])
  useEffect(() => { reload() }, [reload])
  return { seal, sealed: !!seal?.已封口, reload }
}

export function GateBanner({ sealed }) {
  if (!sealed) return null
  return (
    <p className="gate-banner">
      已封口：全日号牌写闸关闭，发号、叫号、过号、办结与号段游标推进一律拒绝；总览与列表只读可查，解封后恢复。
    </p>
  )
}

export function GateChips({ gate }) {
  if (!gate) return null
  return (
    <div className="gate-chips">
      {Object.entries(gate)
        .filter(([k]) => k !== '口径')
        .map(([k, v]) => (
          <span className={`tag ${v === '关' ? 'closed' : ''}`} key={k}>{k}闸 {v}</span>
        ))}
    </div>
  )
}
