import React, { useEffect, useState, useRef } from 'react';
import { api, startLogStream } from '../api';
import Badge from '../components/Badge';

function LogRow({ log }) {
  const levelColors = {
    CRITICAL: 'text-red-500 font-bold bg-red-500/10 border-red-500/20',
    ERROR:    'text-red-400 bg-red-400/10 border-red-400/20',
    WARNING:  'text-amber-400 bg-amber-400/10 border-amber-400/20',
    INFO:     'text-blue-400 bg-blue-400/10 border-blue-400/20',
    DEBUG:    'text-slate-500 bg-slate-500/10 border-slate-500/20',
  };

  // Try to extract timestamp and message if it follows common python logging format:
  // 2026-06-02 10:42:01,123 [INFO] module: message
  const parts = log.raw.match(/^([\d-]+\s[\d:,]+)\s+\[(\w+)\]\s+(.*)$/);
  const timestamp = parts ? parts[1] : '';
  const message = parts ? parts[3] : log.raw;

  return (
    <div className="flex gap-4 px-4 py-1.5 hover:bg-white/5 transition-colors border-b border-white/5 font-mono text-[11px] leading-tight group">
      <span className="shrink-0 text-slate-500 select-none w-32">{timestamp || '—'}</span>
      <span className={`shrink-0 px-1.5 rounded border text-[9px] w-16 text-center h-4 flex items-center justify-center ${levelColors[log.level] || 'text-slate-400 border-white/10'}`}>
        {log.level}
      </span>
      <span className="flex-1 text-slate-300 break-all group-hover:text-white transition-colors">{message}</span>
    </div>
  );
}

export default function Settings() {
  const [mlStatus, setMlStatus] = useState(null);
  const [logs, setLogs] = useState([]);
  const [levelFilter, setLevelFilter] = useState('');
  const [searchTerm, setSearchTerm] = useState('');
  const [isLive, setIsLive] = useState(true);
  
  const logsEndRef = useRef(null);
  const stopStream = useRef(null);

  useEffect(() => {
    fetchMlStatus();
    loadInitialLogs();
    
    if (isLive) {
      startStreaming();
    }

    return () => {
      if (stopStream.current) stopStream.current();
    };
  }, []);

  // Handle live toggle
  useEffect(() => {
    if (isLive && !stopStream.current) {
      startStreaming();
    } else if (!isLive && stopStream.current) {
      stopStream.current();
      stopStream.current = null;
    }
  }, [isLive]);

  useEffect(() => {
    if (isLive) {
      logsEndRef.current?.scrollIntoView({ behavior: 'auto' });
    }
  }, [logs, isLive]);

  const fetchMlStatus = async () => {
    try {
      const data = await api.mlStatus();
      setMlStatus(data);
    } catch (e) { console.error(e); }
  };

  const loadInitialLogs = async () => {
    try {
      const data = await api.logs(200);
      setLogs(data.lines || []);
    } catch (e) { console.error("Snapshot fetch failed", e); }
  };

  const startStreaming = () => {
    if (stopStream.current) stopStream.current();
    stopStream.current = startLogStream(
      0, // tail 0 because we already fetched snapshot
      (event) => setLogs(prev => [...prev, event].slice(-1000)),
      (err) => {
        if (err) console.error("Stream error", err);
        setIsLive(false);
        stopStream.current = null;
      }
    );
  };

  const handleRetrain = async () => {
    if (!window.confirm('Start ML Retraining?')) return;
    try {
      await api.mlRetrain();
      alert('Retraining started!');
      fetchMlStatus();
    } catch (e) { alert(e.message); }
  };

  const filteredLogs = logs.filter(log => {
    const matchesLevel = !levelFilter || log.level === levelFilter;
    const matchesSearch = !searchTerm || log.raw.toLowerCase().includes(searchTerm.toLowerCase());
    return matchesLevel && matchesSearch;
  });

  return (
    <div className="flex flex-col h-[calc(100vh-120px)] gap-6">
      
      <div className="shrink-0 flex justify-between items-start">
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-4 w-1/2 flex items-center justify-between">
          <div>
            <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2 mb-1">
              <span className="text-indigo-600">🧠</span> ML Model Status
            </h3>
            {mlStatus && (
              <div className="flex gap-4 text-[11px]">
                 {Object.entries(mlStatus.files || {}).map(([file, info]) => (
                   <div key={file} className="flex items-center gap-1.5">
                     <span className={`w-1.5 h-1.5 rounded-full ${info.age_hours > 24 ? 'bg-amber-500' : 'bg-emerald-500'}`}></span>
                     <span className="text-slate-500">{file}:</span>
                     <span className="font-bold text-slate-700">{info.age_hours}h</span>
                   </div>
                 ))}
              </div>
            )}
          </div>
          <button onClick={handleRetrain} className="bg-slate-900 hover:bg-slate-800 text-white text-xs font-bold px-4 py-2 rounded-lg transition-colors">
            Retrain
          </button>
        </div>

        <div className="flex gap-2 h-full items-center">
            <Badge color="bg-emerald-100 text-emerald-700">Audit Dashboard Ready</Badge>
        </div>
      </div>

      {/* Modern Audit Console */}
      <div className="flex-1 bg-slate-950 rounded-2xl shadow-2xl flex flex-col overflow-hidden border border-slate-800 ring-1 ring-white/10">
        
        {/* Toolbar */}
        <div className="shrink-0 h-14 border-b border-white/10 flex items-center px-6 gap-6 bg-slate-900/50 backdrop-blur-md">
           <div className="flex items-center gap-2">
             <div className={`w-2 h-2 rounded-full ${isLive ? 'bg-emerald-500 animate-pulse' : 'bg-slate-600'}`}></div>
             <span className="text-[10px] font-black uppercase tracking-widest text-slate-400">Live Tail</span>
           </div>

           <div className="h-6 w-px bg-white/10"></div>

           <div className="flex items-center gap-2">
             <button 
               onClick={() => setIsLive(!isLive)}
               className={`text-[11px] font-bold px-3 py-1 rounded transition-colors ${isLive ? 'bg-red-500/20 text-red-400 hover:bg-red-500/30' : 'bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30'}`}
             >
               {isLive ? 'Pause' : 'Resume'}
             </button>
             <button onClick={() => setLogs([])} className="text-[11px] font-bold px-3 py-1 rounded bg-white/5 text-slate-400 hover:bg-white/10 transition-colors">
               Clear
             </button>
           </div>

           <div className="flex-1"></div>

           <div className="flex items-center gap-4">
             <input 
               type="text" 
               placeholder="Filter logs..." 
               value={searchTerm}
               onChange={e => setSearchTerm(e.target.value)}
               className="bg-white/5 border border-white/10 rounded-lg px-3 py-1 text-xs text-white focus:outline-none focus:ring-1 focus:ring-indigo-500 w-48 transition-all"
             />
             <select 
               value={levelFilter}
               onChange={e => setLevelFilter(e.target.value)}
               className="bg-white/5 border border-white/10 rounded-lg px-3 py-1 text-xs text-slate-300 focus:outline-none focus:ring-1 focus:ring-indigo-500"
             >
               <option value="">All Levels</option>
               <option value="INFO">Info</option>
               <option value="WARNING">Warning</option>
               <option value="ERROR">Error</option>
               <option value="CRITICAL">Critical</option>
             </select>
           </div>
        </div>

        {/* Log Area */}
        <div className="flex-1 overflow-auto custom-scrollbar bg-slate-950">
          <div className="min-h-full py-2">
            {filteredLogs.length === 0 ? (
               <div className="h-full flex items-center justify-center text-slate-600 text-xs italic py-20">
                 No logs found matching criteria.
               </div>
            ) : filteredLogs.map((log, i) => (
              <LogRow key={i} log={log} />
            ))}
            <div ref={logsEndRef} />
          </div>
        </div>

        {/* Status Bar */}
        <div className="shrink-0 h-8 bg-slate-900/80 border-t border-white/10 px-6 flex items-center justify-between">
           <div className="text-[10px] text-slate-500 font-medium">
             Showing {filteredLogs.length} of {logs.length} lines
           </div>
           <div className="text-[10px] text-slate-600 font-mono italic">
             master-backend @ production-lit-db
           </div>
        </div>
      </div>
    </div>
  );
}
