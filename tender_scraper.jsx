import { useState, useCallback, useRef, useEffect } from "react";

const SAMPLE_URLS = [
  "https://www.rfpmart.com/",
  "https://www.tendersontime.com/popular-tenders/virtual-tour-tenders/",
  "https://www.tenderdetail.com/Indian-tender/virtual-tour-tenders",
  "https://sam.gov/search/?keywords=virtual+tour",
];

const KEYWORD_PRESETS = [
  "virtual tour",
  "360 tour",
  "360 photography",
  "virtual reality tour",
  "interactive map",
  "3D walkthrough",
  "panorama",
  "matterport",
  "campus tour",
  "immersive experience",
];

function generateId() {
  return Math.random().toString(36).substring(2, 9);
}

function formatDate(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function escapeCSV(val) {
  const s = String(val ?? "");
  if (s.includes(",") || s.includes('"') || s.includes("\n")) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

function downloadCSV(results) {
  const headers = [
    "Source",
    "Title",
    "Type",
    "RFP_ID",
    "Location",
    "Organization",
    "Description",
    "Budget",
    "Posted_Date",
    "Deadline",
    "Contact_Person",
    "Contact_Email",
    "URL",
    "Status",
    "Relevance",
    "Scraped_At",
  ];
  const rows = results.map((r) =>
    headers.map((h) => escapeCSV(r[h.toLowerCase()] ?? r[h] ?? "")).join(",")
  );
  const csv = [headers.join(","), ...rows].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `virtual_tour_tenders_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

function downloadJSON(results) {
  const blob = new Blob([JSON.stringify(results, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `virtual_tour_tenders_${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

// Animated grid background
function GridBG() {
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 0,
        opacity: 0.04,
        backgroundImage: `
          linear-gradient(rgba(0,255,136,0.5) 1px, transparent 1px),
          linear-gradient(90deg, rgba(0,255,136,0.5) 1px, transparent 1px)
        `,
        backgroundSize: "48px 48px",
        pointerEvents: "none",
      }}
    />
  );
}

function Pill({ children, active, onClick }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "6px 14px",
        borderRadius: "20px",
        border: active ? "1.5px solid #00ff88" : "1px solid #2a3a2e",
        background: active
          ? "linear-gradient(135deg, #00ff8815, #00ff8808)"
          : "#0d1a12",
        color: active ? "#00ff88" : "#6b8a72",
        fontSize: "12px",
        fontFamily: "'JetBrains Mono', monospace",
        cursor: "pointer",
        transition: "all 0.2s",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </button>
  );
}

function StatusBadge({ status }) {
  const color =
    status === "active"
      ? "#00ff88"
      : status === "expired"
        ? "#ff4444"
        : "#ffaa00";
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        fontSize: 10,
        fontFamily: "'JetBrains Mono', monospace",
        color,
        textTransform: "uppercase",
        letterSpacing: "0.1em",
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: color,
          boxShadow: `0 0 6px ${color}50`,
        }}
      />
      {status}
    </span>
  );
}

function RelevanceBadge({ level }) {
  const colors = {
    HIGH: { bg: "#00ff8818", border: "#00ff88", text: "#00ff88" },
    MEDIUM: { bg: "#ffaa0018", border: "#ffaa00", text: "#ffaa00" },
    LOW: { bg: "#ff444418", border: "#ff4444", text: "#ff4444" },
  };
  const c = colors[level] || colors.MEDIUM;
  return (
    <span
      style={{
        padding: "2px 8px",
        borderRadius: 4,
        background: c.bg,
        border: `1px solid ${c.border}40`,
        color: c.text,
        fontSize: 10,
        fontFamily: "'JetBrains Mono', monospace",
        fontWeight: 600,
      }}
    >
      {level}
    </span>
  );
}

function TerminalLine({ prefix, children, color }) {
  return (
    <div
      style={{
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 12,
        color: color || "#6b8a72",
        padding: "2px 0",
        lineHeight: 1.6,
      }}
    >
      <span style={{ color: "#00ff88", marginRight: 8 }}>{prefix}</span>
      {children}
    </div>
  );
}

export default function TenderScraper() {
  const [urls, setUrls] = useState("");
  const [keywords, setKeywords] = useState(["virtual tour", "360 tour"]);
  const [customKeyword, setCustomKeyword] = useState("");
  const [results, setResults] = useState([]);
  const [logs, setLogs] = useState([]);
  const [scraping, setScraping] = useState(false);
  const [activeTab, setActiveTab] = useState("scraper");
  const [expandedRow, setExpandedRow] = useState(null);
  const [savedSearches, setSavedSearches] = useState([]);
  const logRef = useRef(null);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logs]);

  const addLog = useCallback((msg, type = "info") => {
    setLogs((prev) => [
      ...prev,
      { msg, type, time: new Date().toLocaleTimeString() },
    ]);
  }, []);

  const toggleKeyword = (kw) => {
    setKeywords((prev) =>
      prev.includes(kw) ? prev.filter((k) => k !== kw) : [...prev, kw]
    );
  };

  const addCustomKeyword = () => {
    if (customKeyword.trim() && !keywords.includes(customKeyword.trim())) {
      setKeywords((prev) => [...prev, customKeyword.trim()]);
      setCustomKeyword("");
    }
  };

  const scrapeWithAI = async () => {
    const urlList = urls
      .split("\n")
      .map((u) => u.trim())
      .filter((u) => u.length > 0);
    if (urlList.length === 0) {
      addLog("No URLs provided. Add URLs to scrape.", "error");
      return;
    }
    if (keywords.length === 0) {
      addLog("No keywords selected. Pick at least one.", "error");
      return;
    }

    setScraping(true);
    setLogs([]);
    addLog("Initializing scraper engine...", "system");
    addLog(`Target URLs: ${urlList.length}`, "info");
    addLog(`Keywords: ${keywords.join(", ")}`, "info");

    const allResults = [];

    for (let i = 0; i < urlList.length; i++) {
      const url = urlList[i];
      addLog(`[${i + 1}/${urlList.length}] Fetching: ${url}`, "fetch");

      try {
        addLog("Sending to Claude AI for intelligent extraction...", "ai");

        const response = await fetch(
          "https://api.anthropic.com/v1/messages",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              model: "claude-sonnet-4-20250514",
              max_tokens: 1000,
              tools: [
                {
                  type: "web_search_20250305",
                  name: "web_search",
                },
              ],
              messages: [
                {
                  role: "user",
                  content: `You are a tender/RFP data extraction specialist. Search the web for virtual tour related tenders and RFPs from this source: ${url}

Search for tenders matching these keywords: ${keywords.join(", ")}

Find as many relevant tenders as possible and return ONLY a JSON array (no markdown, no backticks, no explanation) with objects containing these fields:
- source: the website name
- title: tender/RFP title
- type: RFP/Tender/Bid/RFI/EOI
- rfp_id: ID number if available
- location: city/state/country
- organization: issuing organization name
- description: brief description (1-2 sentences)
- budget: budget amount or "Not disclosed"
- posted_date: date posted
- deadline: submission deadline
- contact_person: contact name if available, or "Check source"
- contact_email: contact email if available, or "Check source"  
- url: direct link to the tender page
- status: active/expired/unknown
- relevance: HIGH/MEDIUM/LOW based on how closely it matches virtual tour business
- scraped_at: "${new Date().toISOString()}"

Return ONLY the JSON array. If you cannot find tenders, return an empty array [].`,
                },
              ],
            }),
          }
        );

        if (!response.ok) {
          throw new Error(`API error: ${response.status}`);
        }

        const data = await response.json();
        const textBlocks = data.content
          .filter((item) => item.type === "text")
          .map((item) => item.text);
        const fullText = textBlocks.join("\n");

        addLog("Parsing AI response...", "ai");

        try {
          const cleanText = fullText
            .replace(/```json\s*/g, "")
            .replace(/```\s*/g, "")
            .trim();
          const parsed = JSON.parse(cleanText);
          if (Array.isArray(parsed)) {
            parsed.forEach((item) => {
              item.id = generateId();
            });
            allResults.push(...parsed);
            addLog(
              `Extracted ${parsed.length} tenders from ${url}`,
              "success"
            );
          } else {
            addLog("Response was not an array, skipping...", "warn");
          }
        } catch (parseErr) {
          addLog(
            `Parse error for ${url}: ${parseErr.message}`,
            "error"
          );
          addLog("Raw response saved to logs for debugging", "warn");
        }
      } catch (err) {
        addLog(`Error scraping ${url}: ${err.message}`, "error");
      }
    }

    setResults((prev) => [...prev, ...allResults]);
    addLog(`Scraping complete. Total results: ${allResults.length}`, "system");
    setScraping(false);
  };

  const saveSearch = () => {
    const search = {
      id: generateId(),
      urls: urls,
      keywords: [...keywords],
      date: formatDate(new Date()),
      resultCount: results.length,
    };
    setSavedSearches((prev) => [search, ...prev]);
    addLog("Search configuration saved!", "success");
  };

  const loadSearch = (search) => {
    setUrls(search.urls);
    setKeywords(search.keywords);
    addLog("Loaded saved search configuration", "info");
  };

  const clearResults = () => {
    setResults([]);
    setLogs([]);
    setExpandedRow(null);
  };

  const logColors = {
    info: "#6b8a72",
    system: "#00ff88",
    fetch: "#00aaff",
    ai: "#cc88ff",
    success: "#00ff88",
    warn: "#ffaa00",
    error: "#ff4444",
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        background: "#080f0a",
        color: "#c8e6d0",
        fontFamily: "'Space Grotesk', 'Segoe UI', sans-serif",
        position: "relative",
        overflow: "hidden",
      }}
    >
      <link
        href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Space+Grotesk:wght@400;500;600;700&display=swap"
        rel="stylesheet"
      />
      <GridBG />

      {/* Header */}
      <div
        style={{
          position: "relative",
          zIndex: 1,
          borderBottom: "1px solid #1a2e1f",
          padding: "20px 24px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          background:
            "linear-gradient(180deg, #0d1a1210, #080f0a)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <div
            style={{
              width: 40,
              height: 40,
              borderRadius: 10,
              background: "linear-gradient(135deg, #00ff88, #00aa55)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 20,
              fontWeight: 700,
              color: "#080f0a",
              boxShadow: "0 0 20px #00ff8830",
            }}
          >
            ⚡
          </div>
          <div>
            <div
              style={{
                fontSize: 18,
                fontWeight: 700,
                letterSpacing: "-0.02em",
                color: "#e8f5eb",
              }}
            >
              TenderScope
            </div>
            <div
              style={{
                fontSize: 11,
                color: "#4a6b50",
                fontFamily: "'JetBrains Mono', monospace",
                letterSpacing: "0.05em",
              }}
            >
              AI-POWERED VIRTUAL TOUR RFP SCRAPER
            </div>
          </div>
        </div>

        <div style={{ display: "flex", gap: 6 }}>
          {["scraper", "results", "saved"].map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              style={{
                padding: "8px 16px",
                borderRadius: 8,
                border:
                  activeTab === tab
                    ? "1px solid #00ff8860"
                    : "1px solid #1a2e1f",
                background:
                  activeTab === tab ? "#00ff8812" : "transparent",
                color:
                  activeTab === tab ? "#00ff88" : "#4a6b50",
                fontSize: 13,
                fontWeight: 600,
                cursor: "pointer",
                textTransform: "capitalize",
                transition: "all 0.2s",
              }}
            >
              {tab}
              {tab === "results" && results.length > 0 && (
                <span
                  style={{
                    marginLeft: 6,
                    padding: "1px 6px",
                    borderRadius: 10,
                    background: "#00ff8825",
                    fontSize: 10,
                    color: "#00ff88",
                  }}
                >
                  {results.length}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      <div style={{ position: "relative", zIndex: 1, padding: 24 }}>
        {/* SCRAPER TAB */}
        {activeTab === "scraper" && (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: 20,
            }}
          >
            {/* Left: Config */}
            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              {/* URL Input */}
              <div
                style={{
                  background: "#0d1a12",
                  border: "1px solid #1a2e1f",
                  borderRadius: 12,
                  padding: 20,
                }}
              >
                <div
                  style={{
                    fontSize: 13,
                    fontWeight: 600,
                    color: "#00ff88",
                    marginBottom: 10,
                    fontFamily: "'JetBrains Mono', monospace",
                    letterSpacing: "0.05em",
                  }}
                >
                  ▸ TARGET URLs
                </div>
                <textarea
                  value={urls}
                  onChange={(e) => setUrls(e.target.value)}
                  placeholder={"Paste URLs here, one per line...\n\nhttps://www.rfpmart.com/\nhttps://www.tendersontime.com/popular-tenders/virtual-tour-tenders/"}
                  rows={6}
                  style={{
                    width: "100%",
                    background: "#080f0a",
                    border: "1px solid #1a2e1f",
                    borderRadius: 8,
                    padding: 12,
                    color: "#c8e6d0",
                    fontFamily: "'JetBrains Mono', monospace",
                    fontSize: 12,
                    resize: "vertical",
                    outline: "none",
                    boxSizing: "border-box",
                  }}
                />
                <div
                  style={{
                    display: "flex",
                    flexWrap: "wrap",
                    gap: 4,
                    marginTop: 8,
                  }}
                >
                  <span
                    style={{
                      fontSize: 10,
                      color: "#4a6b50",
                      marginRight: 6,
                      lineHeight: "26px",
                    }}
                  >
                    Quick add:
                  </span>
                  {SAMPLE_URLS.map((u) => (
                    <button
                      key={u}
                      onClick={() =>
                        setUrls((prev) =>
                          prev.includes(u)
                            ? prev
                            : prev
                              ? prev + "\n" + u
                              : u
                        )
                      }
                      style={{
                        padding: "3px 8px",
                        borderRadius: 4,
                        border: "1px solid #1a2e1f",
                        background: "#080f0a",
                        color: "#6b8a72",
                        fontSize: 10,
                        cursor: "pointer",
                        fontFamily: "'JetBrains Mono', monospace",
                      }}
                    >
                      {new URL(u).hostname.replace("www.", "")}
                    </button>
                  ))}
                </div>
              </div>

              {/* Keywords */}
              <div
                style={{
                  background: "#0d1a12",
                  border: "1px solid #1a2e1f",
                  borderRadius: 12,
                  padding: 20,
                }}
              >
                <div
                  style={{
                    fontSize: 13,
                    fontWeight: 600,
                    color: "#00ff88",
                    marginBottom: 10,
                    fontFamily: "'JetBrains Mono', monospace",
                    letterSpacing: "0.05em",
                  }}
                >
                  ▸ SEARCH KEYWORDS
                </div>
                <div
                  style={{
                    display: "flex",
                    flexWrap: "wrap",
                    gap: 6,
                    marginBottom: 10,
                  }}
                >
                  {KEYWORD_PRESETS.map((kw) => (
                    <Pill
                      key={kw}
                      active={keywords.includes(kw)}
                      onClick={() => toggleKeyword(kw)}
                    >
                      {kw}
                    </Pill>
                  ))}
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <input
                    value={customKeyword}
                    onChange={(e) => setCustomKeyword(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && addCustomKeyword()}
                    placeholder="Add custom keyword..."
                    style={{
                      flex: 1,
                      background: "#080f0a",
                      border: "1px solid #1a2e1f",
                      borderRadius: 8,
                      padding: "8px 12px",
                      color: "#c8e6d0",
                      fontFamily: "'JetBrains Mono', monospace",
                      fontSize: 12,
                      outline: "none",
                    }}
                  />
                  <button
                    onClick={addCustomKeyword}
                    style={{
                      padding: "8px 14px",
                      borderRadius: 8,
                      border: "1px solid #1a2e1f",
                      background: "#0d1a12",
                      color: "#00ff88",
                      fontSize: 12,
                      cursor: "pointer",
                    }}
                  >
                    + Add
                  </button>
                </div>
              </div>

              {/* Action Buttons */}
              <div style={{ display: "flex", gap: 10 }}>
                <button
                  onClick={scrapeWithAI}
                  disabled={scraping}
                  style={{
                    flex: 1,
                    padding: "14px 24px",
                    borderRadius: 10,
                    border: "none",
                    background: scraping
                      ? "#1a2e1f"
                      : "linear-gradient(135deg, #00ff88, #00cc66)",
                    color: scraping ? "#4a6b50" : "#080f0a",
                    fontSize: 14,
                    fontWeight: 700,
                    cursor: scraping ? "not-allowed" : "pointer",
                    fontFamily: "'Space Grotesk', sans-serif",
                    letterSpacing: "0.02em",
                    transition: "all 0.3s",
                    boxShadow: scraping ? "none" : "0 0 30px #00ff8825",
                  }}
                >
                  {scraping ? "⟳ Scraping..." : "⚡ Start AI Scrape"}
                </button>
                <button
                  onClick={saveSearch}
                  style={{
                    padding: "14px 18px",
                    borderRadius: 10,
                    border: "1px solid #1a2e1f",
                    background: "#0d1a12",
                    color: "#6b8a72",
                    fontSize: 13,
                    cursor: "pointer",
                    fontWeight: 600,
                  }}
                >
                  💾 Save
                </button>
                <button
                  onClick={clearResults}
                  style={{
                    padding: "14px 18px",
                    borderRadius: 10,
                    border: "1px solid #1a2e1f",
                    background: "#0d1a12",
                    color: "#6b8a72",
                    fontSize: 13,
                    cursor: "pointer",
                    fontWeight: 600,
                  }}
                >
                  🗑 Clear
                </button>
              </div>
            </div>

            {/* Right: Live Terminal */}
            <div
              style={{
                background: "#0a110d",
                border: "1px solid #1a2e1f",
                borderRadius: 12,
                display: "flex",
                flexDirection: "column",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  padding: "10px 16px",
                  borderBottom: "1px solid #1a2e1f",
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                }}
              >
                <div
                  style={{
                    display: "flex",
                    gap: 5,
                  }}
                >
                  <div
                    style={{
                      width: 10,
                      height: 10,
                      borderRadius: "50%",
                      background: "#ff4444",
                    }}
                  />
                  <div
                    style={{
                      width: 10,
                      height: 10,
                      borderRadius: "50%",
                      background: "#ffaa00",
                    }}
                  />
                  <div
                    style={{
                      width: 10,
                      height: 10,
                      borderRadius: "50%",
                      background: "#00ff88",
                    }}
                  />
                </div>
                <span
                  style={{
                    fontSize: 11,
                    color: "#4a6b50",
                    fontFamily: "'JetBrains Mono', monospace",
                  }}
                >
                  tenderscope — live scraper output
                </span>
              </div>
              <div
                ref={logRef}
                style={{
                  flex: 1,
                  padding: 16,
                  overflowY: "auto",
                  maxHeight: 420,
                  minHeight: 420,
                }}
              >
                {logs.length === 0 && (
                  <div
                    style={{
                      color: "#2a3a2e",
                      fontFamily: "'JetBrains Mono', monospace",
                      fontSize: 12,
                      textAlign: "center",
                      marginTop: 80,
                    }}
                  >
                    <div style={{ fontSize: 32, marginBottom: 12, opacity: 0.3 }}>
                      ⚡
                    </div>
                    Waiting for scrape command...
                    <br />
                    Add URLs and keywords, then hit Start
                  </div>
                )}
                {logs.map((log, i) => (
                  <div
                    key={i}
                    style={{
                      fontFamily: "'JetBrains Mono', monospace",
                      fontSize: 11,
                      color: logColors[log.type] || "#6b8a72",
                      padding: "3px 0",
                      lineHeight: 1.6,
                      borderLeft: `2px solid ${logColors[log.type] || "#1a2e1f"}20`,
                      paddingLeft: 10,
                    }}
                  >
                    <span style={{ color: "#2a3a2e", marginRight: 8 }}>
                      [{log.time}]
                    </span>
                    {log.msg}
                  </div>
                ))}
                {scraping && (
                  <div
                    style={{
                      color: "#00ff88",
                      fontFamily: "'JetBrains Mono', monospace",
                      fontSize: 11,
                      animation: "pulse 1.5s ease-in-out infinite",
                    }}
                  >
                    ▊
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* RESULTS TAB */}
        {activeTab === "results" && (
          <div>
            {/* Export Bar */}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                marginBottom: 16,
                padding: "12px 16px",
                background: "#0d1a12",
                borderRadius: 10,
                border: "1px solid #1a2e1f",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <span
                  style={{
                    fontFamily: "'JetBrains Mono', monospace",
                    fontSize: 12,
                    color: "#4a6b50",
                  }}
                >
                  {results.length} tenders found
                </span>
                <span style={{ color: "#1a2e1f" }}>|</span>
                <span style={{ fontSize: 11, color: "#4a6b50" }}>
                  HIGH:{" "}
                  {
                    results.filter(
                      (r) =>
                        (r.relevance || r.Relevance || "").toUpperCase() ===
                        "HIGH"
                    ).length
                  }
                  &nbsp; MED:{" "}
                  {
                    results.filter(
                      (r) =>
                        (r.relevance || r.Relevance || "").toUpperCase() ===
                        "MEDIUM"
                    ).length
                  }
                </span>
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  onClick={() => downloadCSV(results)}
                  disabled={results.length === 0}
                  style={{
                    padding: "8px 16px",
                    borderRadius: 8,
                    border: "1px solid #00ff8840",
                    background: "#00ff8810",
                    color: "#00ff88",
                    fontSize: 12,
                    fontWeight: 600,
                    cursor:
                      results.length === 0 ? "not-allowed" : "pointer",
                    opacity: results.length === 0 ? 0.4 : 1,
                  }}
                >
                  ↓ Download CSV
                </button>
                <button
                  onClick={() => downloadJSON(results)}
                  disabled={results.length === 0}
                  style={{
                    padding: "8px 16px",
                    borderRadius: 8,
                    border: "1px solid #1a2e1f",
                    background: "#0d1a12",
                    color: "#6b8a72",
                    fontSize: 12,
                    fontWeight: 600,
                    cursor:
                      results.length === 0 ? "not-allowed" : "pointer",
                    opacity: results.length === 0 ? 0.4 : 1,
                  }}
                >
                  ↓ Download JSON
                </button>
              </div>
            </div>

            {/* Results List */}
            {results.length === 0 ? (
              <div
                style={{
                  textAlign: "center",
                  padding: 80,
                  color: "#2a3a2e",
                  fontFamily: "'JetBrains Mono', monospace",
                }}
              >
                <div style={{ fontSize: 40, marginBottom: 12, opacity: 0.3 }}>
                  📋
                </div>
                No results yet. Run a scrape first.
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {results.map((r, i) => {
                  const isExpanded = expandedRow === i;
                  return (
                    <div
                      key={r.id || i}
                      onClick={() =>
                        setExpandedRow(isExpanded ? null : i)
                      }
                      style={{
                        background: "#0d1a12",
                        border: isExpanded
                          ? "1px solid #00ff8830"
                          : "1px solid #1a2e1f",
                        borderRadius: 10,
                        padding: 16,
                        cursor: "pointer",
                        transition: "all 0.2s",
                      }}
                    >
                      <div
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "flex-start",
                          gap: 12,
                        }}
                      >
                        <div style={{ flex: 1 }}>
                          <div
                            style={{
                              display: "flex",
                              alignItems: "center",
                              gap: 8,
                              marginBottom: 6,
                            }}
                          >
                            <RelevanceBadge
                              level={(
                                r.relevance ||
                                r.Relevance ||
                                "MEDIUM"
                              ).toUpperCase()}
                            />
                            <StatusBadge
                              status={(
                                r.status ||
                                r.Status ||
                                "unknown"
                              ).toLowerCase()}
                            />
                            <span
                              style={{
                                fontSize: 10,
                                color: "#4a6b50",
                                fontFamily:
                                  "'JetBrains Mono', monospace",
                                background: "#080f0a",
                                padding: "2px 6px",
                                borderRadius: 4,
                              }}
                            >
                              {r.type || r.Type || "RFP"}
                            </span>
                          </div>
                          <div
                            style={{
                              fontSize: 14,
                              fontWeight: 600,
                              color: "#e8f5eb",
                              marginBottom: 4,
                            }}
                          >
                            {r.title || r.Title || "Untitled"}
                          </div>
                          <div
                            style={{
                              fontSize: 12,
                              color: "#6b8a72",
                              display: "flex",
                              gap: 16,
                              fontFamily:
                                "'JetBrains Mono', monospace",
                            }}
                          >
                            <span>
                              📍 {r.location || r.Location || "N/A"}
                            </span>
                            <span>
                              🏢 {r.organization || r.Organization || "N/A"}
                            </span>
                            <span>
                              🗓{" "}
                              {r.deadline || r.Deadline || "N/A"}
                            </span>
                          </div>
                        </div>
                        <span
                          style={{
                            color: "#2a3a2e",
                            fontSize: 16,
                            transition: "transform 0.2s",
                            transform: isExpanded
                              ? "rotate(180deg)"
                              : "rotate(0deg)",
                          }}
                        >
                          ▾
                        </span>
                      </div>

                      {isExpanded && (
                        <div
                          style={{
                            marginTop: 14,
                            paddingTop: 14,
                            borderTop: "1px solid #1a2e1f",
                            display: "grid",
                            gridTemplateColumns: "1fr 1fr",
                            gap: "8px 24px",
                            fontFamily:
                              "'JetBrains Mono', monospace",
                            fontSize: 11,
                          }}
                        >
                          <TerminalLine prefix="ID:">
                            {r.rfp_id || r.RFP_ID || "N/A"}
                          </TerminalLine>
                          <TerminalLine prefix="Source:">
                            {r.source || r.Source || "N/A"}
                          </TerminalLine>
                          <TerminalLine prefix="Budget:">
                            {r.budget || r.Budget || "Not disclosed"}
                          </TerminalLine>
                          <TerminalLine prefix="Posted:">
                            {r.posted_date ||
                              r.Posted_Date ||
                              "N/A"}
                          </TerminalLine>
                          <TerminalLine prefix="Contact:">
                            {r.contact_person ||
                              r.Contact_Person ||
                              "Check source"}
                          </TerminalLine>
                          <TerminalLine prefix="Email:">
                            {r.contact_email ||
                              r.Contact_Email ||
                              "Check source"}
                          </TerminalLine>
                          <div
                            style={{ gridColumn: "1 / -1" }}
                          >
                            <TerminalLine prefix="Desc:">
                              {r.description ||
                                r.Description ||
                                "N/A"}
                            </TerminalLine>
                          </div>
                          {(r.url || r.URL) && (
                            <div
                              style={{ gridColumn: "1 / -1" }}
                            >
                              <TerminalLine prefix="Link:">
                                <a
                                  href={r.url || r.URL}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  onClick={(e) =>
                                    e.stopPropagation()
                                  }
                                  style={{
                                    color: "#00aaff",
                                    textDecoration: "none",
                                    wordBreak: "break-all",
                                  }}
                                >
                                  {r.url || r.URL}
                                </a>
                              </TerminalLine>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* SAVED TAB */}
        {activeTab === "saved" && (
          <div>
            {savedSearches.length === 0 ? (
              <div
                style={{
                  textAlign: "center",
                  padding: 80,
                  color: "#2a3a2e",
                  fontFamily: "'JetBrains Mono', monospace",
                }}
              >
                <div style={{ fontSize: 40, marginBottom: 12, opacity: 0.3 }}>
                  💾
                </div>
                No saved searches. Configure a scrape and hit Save.
              </div>
            ) : (
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 8,
                }}
              >
                {savedSearches.map((s) => (
                  <div
                    key={s.id}
                    style={{
                      background: "#0d1a12",
                      border: "1px solid #1a2e1f",
                      borderRadius: 10,
                      padding: 16,
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                    }}
                  >
                    <div>
                      <div
                        style={{
                          fontSize: 12,
                          color: "#6b8a72",
                          fontFamily:
                            "'JetBrains Mono', monospace",
                          marginBottom: 4,
                        }}
                      >
                        {s.date}
                      </div>
                      <div
                        style={{
                          fontSize: 13,
                          color: "#c8e6d0",
                          marginBottom: 4,
                        }}
                      >
                        {s.keywords.join(", ")}
                      </div>
                      <div
                        style={{
                          fontSize: 11,
                          color: "#4a6b50",
                          fontFamily:
                            "'JetBrains Mono', monospace",
                        }}
                      >
                        {
                          s.urls
                            .split("\n")
                            .filter((u) => u.trim()).length
                        }{" "}
                        URLs · {s.resultCount} results
                      </div>
                    </div>
                    <button
                      onClick={() => {
                        loadSearch(s);
                        setActiveTab("scraper");
                      }}
                      style={{
                        padding: "8px 16px",
                        borderRadius: 8,
                        border: "1px solid #00ff8840",
                        background: "#00ff8810",
                        color: "#00ff88",
                        fontSize: 12,
                        fontWeight: 600,
                        cursor: "pointer",
                      }}
                    >
                      Load & Run
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.3; }
        }
        textarea::placeholder, input::placeholder {
          color: #2a3a2e;
        }
        ::-webkit-scrollbar {
          width: 6px;
        }
        ::-webkit-scrollbar-track {
          background: #080f0a;
        }
        ::-webkit-scrollbar-thumb {
          background: #1a2e1f;
          border-radius: 3px;
        }
        ::-webkit-scrollbar-thumb:hover {
          background: #2a3a2e;
        }
      `}</style>
    </div>
  );
}
