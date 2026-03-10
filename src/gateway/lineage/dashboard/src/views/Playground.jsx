import { useState, useEffect } from 'react';
import { getHealth, getExecution } from '../api';
import { displayModel, verdictBadgeClass } from '../utils';

const DEFAULT_MODELS = ['gpt-4', 'gpt-4o', 'gpt-3.5-turbo', 'claude-3-opus-20240229', 'claude-sonnet-4-20250514'];

function GovernanceReadout({ meta, record, loading }) {
  if (loading) return <div className="skeleton-block" style={{ height: 100 }} />;
  if (!meta && !record) return null;

  const decisions = record?.metadata?.analyzer_decisions || [];

  return (
    <div className="pg-governance">
      <div className="pg-section-label">◆ Governance Readout</div>
      <div className="pg-gov-grid">
        {meta?.executionId && <><span className="pg-gov-key">EXEC</span><span className="pg-gov-val mono">{meta.executionId}</span></>}
        {meta?.attestationId && <><span className="pg-gov-key">ATTEST</span><span className="pg-gov-val mono">{meta.attestationId}</span></>}
        {meta?.policyResult && (
          <><span className="pg-gov-key">POLICY</span><span className="pg-gov-val"><span className={`badge ${meta.policyResult === 'allow' ? 'badge-pass' : 'badge-fail'}`}>{meta.policyResult}</span></span></>
        )}
        {meta?.chainSeq != null && <><span className="pg-gov-key">CHAIN</span><span className="pg-gov-val mono">seq #{meta.chainSeq}</span></>}
        {record?.latency_ms != null && <><span className="pg-gov-key">LATENCY</span><span className="pg-gov-val mono">{record.latency_ms.toFixed(0)}ms</span></>}
        {(record?.prompt_tokens > 0 || record?.completion_tokens > 0) && (
          <><span className="pg-gov-key">TOKENS</span><span className="pg-gov-val mono">{record.prompt_tokens} in / {record.completion_tokens} out</span></>
        )}
        {record?.cache_hit && (
          <><span className="pg-gov-key">CACHE</span><span className="pg-gov-val"><span className="badge badge-gold">HIT</span> <span className="mono">{record.cached_tokens} tokens</span></span></>
        )}
      </div>
      {decisions.length > 0 && (
        <div className="pg-gov-analysis">
          <span className="pg-gov-key" style={{ marginRight: 8 }}>ANALYSIS</span>
          {decisions.map((d, i) => (
            <span key={i} style={{ marginRight: 8 }}>
              <span className={`badge ${verdictBadgeClass(d.verdict)}`}>{d.verdict}</span>
              <span className="mono" style={{ fontSize: 10, color: 'var(--text-muted)', marginLeft: 4 }}>{d.analyzer_id}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function ResponsePane({ label, response, loading, error, governanceMeta, governanceRecord, govLoading }) {
  return (
    <div className="pg-response-pane">
      {label && <div className="pg-pane-label">{label}</div>}
      <div className="pg-response-body">
        {loading && (
          <div className="pg-response-loading">
            <div className="pg-loading-bar" />
            <span>Awaiting provider response...</span>
          </div>
        )}
        {error && <div className="error-card" style={{ margin: 0 }}>{error}</div>}
        {!loading && !error && response && (
          <div className="pg-response-text">{response}</div>
        )}
        {!loading && !error && !response && (
          <div className="pg-response-empty">
            <div className="pg-response-empty-icon">◇</div>
            <div>Every request here generates a real audit record.</div>
            <div style={{ fontSize: 11, marginTop: 4 }}>Send a prompt to begin.</div>
          </div>
        )}
      </div>
      <GovernanceReadout meta={governanceMeta} record={governanceRecord} loading={govLoading} />
    </div>
  );
}

export default function Playground({ navigate }) {
  const [models, setModels] = useState(DEFAULT_MODELS);
  const [compare, setCompare] = useState(false);
  const [modelA, setModelA] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [userPrompt, setUserPrompt] = useState('');
  const [temperature, setTemperature] = useState(0.7);
  const [maxTokens, setMaxTokens] = useState(1024);

  const [responseA, setResponseA] = useState(null);
  const [loadingA, setLoadingA] = useState(false);
  const [errorA, setErrorA] = useState(null);
  const [govMetaA, setGovMetaA] = useState(null);
  const [govRecordA, setGovRecordA] = useState(null);
  const [govLoadingA, setGovLoadingA] = useState(false);

  const [modelB, setModelB] = useState('');
  const [responseB, setResponseB] = useState(null);
  const [loadingB, setLoadingB] = useState(false);
  const [errorB, setErrorB] = useState(null);
  const [govMetaB, setGovMetaB] = useState(null);
  const [govRecordB, setGovRecordB] = useState(null);
  const [govLoadingB, setGovLoadingB] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const h = await getHealth();
        const caps = h?.model_capabilities;
        if (caps && Object.keys(caps).length > 0) {
          setModels(prev => [...new Set([...Object.keys(caps), ...prev])]);
        }
      } catch {}
    })();
  }, []);

  useEffect(() => {
    if (!modelA && models.length > 0) setModelA(models[0]);
    if (!modelB && models.length > 1) setModelB(models[1]);
  }, [models]);

  const sendRequest = async (model, setResponse, setLoading, setError, setGovMeta, setGovRecord, setGovLoading) => {
    if (!userPrompt.trim()) return;
    setLoading(true);
    setError(null);
    setResponse(null);
    setGovMeta(null);
    setGovRecord(null);

    const messages = [];
    if (systemPrompt.trim()) messages.push({ role: 'system', content: systemPrompt.trim() });
    messages.push({ role: 'user', content: userPrompt.trim() });

    try {
      const resp = await fetch('/v1/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model,
          messages,
          temperature: parseFloat(temperature),
          max_tokens: parseInt(maxTokens, 10),
        }),
      });

      const meta = {
        executionId: resp.headers.get('x-walacor-execution-id'),
        attestationId: resp.headers.get('x-walacor-attestation-id'),
        policyResult: resp.headers.get('x-walacor-policy-result'),
        chainSeq: resp.headers.get('x-walacor-chain-seq'),
      };
      setGovMeta(meta);

      if (!resp.ok) {
        setError(`HTTP ${resp.status}: ${await resp.text()}`);
        setLoading(false);
        return;
      }

      const data = await resp.json();
      const content = data?.choices?.[0]?.message?.content || data?.choices?.[0]?.text || JSON.stringify(data);
      setResponse(content);
      setLoading(false);

      if (meta.executionId) {
        setGovLoading(true);
        try {
          const execData = await getExecution(meta.executionId);
          setGovRecord(execData?.record || null);
        } catch {}
        setGovLoading(false);
      }
    } catch (e) {
      setError(e.message);
      setLoading(false);
    }
  };

  const handleSend = () => {
    sendRequest(modelA, setResponseA, setLoadingA, setErrorA, setGovMetaA, setGovRecordA, setGovLoadingA);
    if (compare && modelB) {
      sendRequest(modelB, setResponseB, setLoadingB, setErrorB, setGovMetaB, setGovRecordB, setGovLoadingB);
    }
  };

  return (
    <div className="fade-child">
      {/* Input controls */}
      <div className="pg-controls card">
        <div className="pg-controls-header">
          <div className="pg-section-label" style={{ marginBottom: 0 }}>◆ Prompt Playground</div>
          <button
            className={`pg-compare-toggle ${compare ? 'active' : ''}`}
            onClick={() => setCompare(!compare)}
          >
            <span className="pg-compare-icon">{compare ? '◆◆' : '◇◇'}</span>
            {compare ? 'Comparison Active' : 'Compare Models'}
          </button>
        </div>

        {/* Model selectors */}
        <div className="pg-model-row">
          <div className="pg-field">
            <label className="pg-label">Model {compare ? 'A' : ''}</label>
            <select value={modelA} onChange={e => setModelA(e.target.value)} className="pg-select">
              {models.map(m => <option key={m} value={m}>{displayModel(m)}</option>)}
            </select>
          </div>
          {compare && (
            <div className="pg-field">
              <label className="pg-label">Model B</label>
              <select value={modelB} onChange={e => setModelB(e.target.value)} className="pg-select">
                {models.map(m => <option key={m} value={m}>{displayModel(m)}</option>)}
              </select>
            </div>
          )}
        </div>

        {/* System prompt */}
        <div className="pg-field">
          <label className="pg-label">System Prompt <span style={{ opacity: 0.5 }}>(optional)</span></label>
          <textarea
            value={systemPrompt}
            onChange={e => setSystemPrompt(e.target.value)}
            className="pg-textarea"
            rows={2}
            placeholder="You are a helpful assistant..."
          />
        </div>

        {/* User prompt */}
        <div className="pg-field">
          <label className="pg-label">User Prompt</label>
          <textarea
            value={userPrompt}
            onChange={e => setUserPrompt(e.target.value)}
            className="pg-textarea pg-textarea-main"
            rows={5}
            placeholder="Type your prompt here..."
            onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleSend(); }}
          />
        </div>

        {/* Parameters + send */}
        <div className="pg-params-row">
          <div className="pg-param">
            <label className="pg-label">Temperature</label>
            <div className="pg-param-control">
              <input type="range" min="0" max="2" step="0.1" value={temperature} onChange={e => setTemperature(e.target.value)} className="pg-slider" />
              <span className="pg-param-value">{parseFloat(temperature).toFixed(1)}</span>
            </div>
          </div>
          <div className="pg-param">
            <label className="pg-label">Max Tokens</label>
            <input
              type="number" min="1" max="128000" value={maxTokens}
              onChange={e => setMaxTokens(e.target.value)}
              className="pg-number-input"
            />
          </div>
          <button
            onClick={handleSend}
            disabled={!userPrompt.trim() || loadingA || loadingB}
            className="pg-send-btn"
          >
            <span className="pg-send-icon">▶</span>
            {loadingA || loadingB ? 'Processing...' : 'Send'}
            {!loadingA && !loadingB && <span className="pg-send-hint">⌘↵</span>}
          </button>
        </div>
      </div>

      {/* Response area */}
      <div className={`pg-results ${compare ? 'pg-results-compare' : ''}`}>
        <ResponsePane
          label={compare ? 'Model A' : null}
          response={responseA} loading={loadingA} error={errorA}
          governanceMeta={govMetaA} governanceRecord={govRecordA} govLoading={govLoadingA}
        />
        {compare && (
          <ResponsePane
            label="Model B"
            response={responseB} loading={loadingB} error={errorB}
            governanceMeta={govMetaB} governanceRecord={govRecordB} govLoading={govLoadingB}
          />
        )}
      </div>
    </div>
  );
}
