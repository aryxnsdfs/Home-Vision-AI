import React from "react";
import { Document, Image, Page, StyleSheet, Text, View, Svg, Rect, Line as SvgLine, Polygon, Circle, Path } from "@react-pdf/renderer";

const BLUE = "#0b3d91";
const WHITE = "#ffffff";
const INK = "#0f172a";
const MUTED = "#64748b";
const LINE = "#cbd5e1";
const GRID = "#6ea0d6";

const formatRupees = (value) => `Rs. ${Number(value).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
const stampDate = () => new Date().toLocaleDateString("en-IN");
const up = (value) => String(value ?? "").toUpperCase();
const sanitizeImageSrc = (value) => {
  if (typeof value !== "string") return null;
  const clean = value.trim().replace(/\s/g, "");
  if (/^data:image\/(png|jpeg|jpg);base64,[A-Za-z0-9+/=]+$/i.test(clean)) return clean;
  if (/^[A-Za-z0-9+/=]+$/.test(clean)) return `data:image/jpeg;base64,${clean}`;
  return null;
};

const styles = StyleSheet.create({
  page: {
    position: "relative",
    paddingTop: 30,
    paddingLeft: 30,
    paddingRight: 30,
    paddingBottom: 20,
    backgroundColor: "#f8fafc",
    color: INK,
    fontFamily: "Helvetica"
  },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    borderBottom: `1 solid ${INK}`,
    paddingBottom: 8,
    marginBottom: 12
  },
  firm: {
    fontSize: 16,
    fontWeight: 700,
    letterSpacing: 1.1
  },
  sheet: {
    fontSize: 8,
    letterSpacing: 0.7,
    textAlign: "right",
    color: MUTED
  },
  title: {
    fontSize: 19,
    fontWeight: 700,
    letterSpacing: 0.6,
    marginBottom: 8
  },
  subtitle: {
    fontSize: 8.5,
    color: MUTED,
    letterSpacing: 0.4,
    marginBottom: 10
  },
  blueprintPanel: {
    position: "relative",
    backgroundColor: BLUE,
    border: `1.3 solid ${INK}`,
    padding: 10,
    marginBottom: 12,
    height: 280
  },
  blueprintGridV: {
    position: "absolute",
    top: 0,
    bottom: 0,
    width: 0,
    borderLeft: `0.45 solid ${GRID}`
  },
  blueprintGridH: {
    position: "absolute",
    left: 0,
    right: 0,
    height: 0,
    borderTop: `0.45 solid ${GRID}`
  },
  viewGrid: {
    flexDirection: "row",
    gap: 10,
    marginBottom: 12
  },
  viewCard: {
    flex: 1,
    border: `1 solid ${LINE}`,
    padding: 8,
    backgroundColor: WHITE
  },
  viewImage: {
    width: "100%",
    height: 100,
    objectFit: "cover",
    border: `1 solid ${LINE}`,
    marginBottom: 4
  },
  metrics: {
    flexDirection: "row",
    borderTop: `1 solid ${INK}`,
    borderLeft: `1 solid ${INK}`,
    marginBottom: 12
  },
  metricCell: {
    flex: 1,
    minHeight: 48,
    borderRight: `1 solid ${INK}`,
    borderBottom: `1 solid ${INK}`,
    padding: 8,
    backgroundColor: WHITE
  },
  metricLabel: {
    fontSize: 7,
    color: MUTED,
    letterSpacing: 0.8,
    marginBottom: 4
  },
  metricValue: {
    fontSize: 11,
    fontWeight: 700
  },
  sectionTitle: {
    fontSize: 10.5,
    fontWeight: 700,
    letterSpacing: 0.8,
    marginTop: 8,
    marginBottom: 6,
    borderBottom: `1 solid ${INK}`,
    paddingBottom: 4
  },
  specGrid: {
    flexDirection: "row",
    gap: 10
  },
  specPanel: {
    flex: 1,
    border: `1 solid ${INK}`,
    backgroundColor: WHITE
  },
  specRow: {
    flexDirection: "row",
    borderBottom: `0.7 solid ${LINE}`,
    minHeight: 22
  },
  specLabel: {
    width: "42%",
    borderRight: `0.7 solid ${LINE}`,
    padding: 5,
    fontSize: 7,
    color: MUTED,
    letterSpacing: 0.6
  },
  specValue: {
    flex: 1,
    padding: 5,
    fontSize: 8,
    fontWeight: 700
  },
  table: {
    borderTop: `1 solid ${INK}`,
    borderLeft: `1 solid ${INK}`,
    backgroundColor: WHITE,
    marginBottom: 6
  },
  tableRow: {
    flexDirection: "row",
    minHeight: 17
  },
  tableHead: {
    fontWeight: 700,
    letterSpacing: 0.5
  },
  c1: { width: "35%", borderRight: `0.7 solid ${LINE}`, borderBottom: `0.7 solid ${LINE}`, padding: 4, fontSize: 7 },
  c2: { width: "15%", borderRight: `0.7 solid ${LINE}`, borderBottom: `0.7 solid ${LINE}`, padding: 4, fontSize: 7 },
  c3: { width: "15%", borderRight: `0.7 solid ${LINE}`, borderBottom: `0.7 solid ${LINE}`, padding: 4, fontSize: 7 },
  c4: { width: "15%", borderRight: `0.7 solid ${LINE}`, borderBottom: `0.7 solid ${LINE}`, padding: 4, fontSize: 7, textAlign: "right" },
  c5: { width: "20%", borderRight: `0.7 solid ${LINE}`, borderBottom: `0.7 solid ${LINE}`, padding: 4, fontSize: 7, textAlign: "right" },
  titleBlock: {
    width: 265,
    borderTop: `1.2 solid ${INK}`,
    borderLeft: `1.2 solid ${INK}`,
    backgroundColor: WHITE,
    position: "absolute",
    bottom: 20,
    right: 30
  },
  titleBlockRow: {
    flexDirection: "row",
    minHeight: 17
  },
  titleBlockLabel: {
    width: "34%",
    borderRight: `1 solid ${INK}`,
    borderBottom: `1 solid ${INK}`,
    padding: 4,
    fontSize: 6.5,
    color: MUTED,
    letterSpacing: 0.8
  },
  titleBlockValue: {
    flex: 1,
    borderRight: `1 solid ${INK}`,
    borderBottom: `1 solid ${INK}`,
    padding: 4,
    fontSize: 7.2,
    fontWeight: 700
  },
  footerNote: {
    position: "absolute",
    bottom: 30,
    left: 30,
    width: 220,
    color: MUTED,
    fontSize: 6.5,
    letterSpacing: 0.5,
    lineHeight: 1.35
  },
  pageNum: {
    position: "absolute",
    bottom: 12,
    left: 0,
    right: 0,
    textAlign: "center",
    fontSize: 7,
    color: MUTED
  },
  stampBox: {
    position: "absolute",
    bottom: 110,
    right: 30,
    width: 265,
    height: 60,
    border: `1 solid ${INK}`,
    justifyContent: "center",
    alignItems: "center",
    padding: 10
  },
  stampText: {
    fontSize: 8,
    fontWeight: 700,
    color: INK,
    textAlign: "center",
    letterSpacing: 0.5
  }
});

const CheckItem = ({ text }) => (
  <View style={{ flexDirection: "row", alignItems: "center", marginBottom: 7 }}>
    <Svg viewBox="0 0 24 24" width={13} height={13} style={{ marginRight: 7 }}>
      <Circle cx={12} cy={12} r={11} fill="#dcfce7" />
      <Path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z" fill="#16a34a" />
    </Svg>
    <Text style={{ fontSize: 10, color: "#15803d", fontWeight: "bold" }}>Complete: {text}</Text>
  </View>
);

const PendingItem = ({ text }) => (
  <View style={{ flexDirection: "row", alignItems: "center", marginBottom: 7 }}>
    <Svg viewBox="0 0 24 24" width={13} height={13} style={{ marginRight: 7 }}>
      <Rect x={2} y={2} width={20} height={20} rx={3} fill="#fef3c7" stroke="#d97706" strokeWidth={2} />
    </Svg>
    <Text style={{ fontSize: 10, color: "#b45309" }}>Pending: {text}</Text>
  </View>
);

function BlueprintGrid() {
  return (
    <>
      {Array.from({ length: 14 }).map((_, index) => (
        <View key={`v-${index}`} style={[styles.blueprintGridV, { left: `${index * 7.7}%` }]} />
      ))}
      {Array.from({ length: 10 }).map((_, index) => (
        <View key={`h-${index}`} style={[styles.blueprintGridH, { top: `${index * 11}%` }]} />
      ))}
    </>
  );
}

function TitleBlock({ project, sheet, title }) {
  const rows = [
    ["PROJECT", up(project.name)],
    ["FIRM", "HOME VISION AI"],
    ["SCALE", "NTS"],
    ["DATE", stampDate()],
    ["SHEET", `${sheet} / ${title}`],
    ["EXPORT ID", project.id]
  ];
  return (
    <View style={styles.titleBlock} wrap={false}>
      {rows.map(([label, value]) => (
        <View key={label} style={styles.titleBlockRow} wrap={false}>
          <Text style={styles.titleBlockLabel}>{label}</Text>
          <Text style={styles.titleBlockValue}>{value}</Text>
        </View>
      ))}
    </View>
  );
}

const SpecRow = ({ label, value }) => (
  <View style={styles.specRow}>
    <Text style={styles.specLabel}>{up(label)}</Text>
    <Text style={styles.specValue}>{up(value)}</Text>
  </View>
);

function Header({ project, sheet }) {
  return (
    <View style={styles.header} fixed>
      <View style={{ flexDirection: "row", alignItems: "center" }}>
        <Image src="/logo.png" style={{ width: 28, height: 28, marginRight: 8 }} />
        <View>
          <Text style={styles.firm}>HOME VISION AI</Text>
          <Text style={styles.subtitle}>{up(project.location.city)}, {up(project.location.state)} – ZONE {project.location.seismicZone}</Text>
        </View>
      </View>
      <View>
        <Text style={styles.sheet}>ARCHITECTURAL EXPORT</Text>
        <Text style={styles.sheet}>SHEET {sheet}</Text>
      </View>
    </View>
  );
}

function SnapshotImage({ src, label }) {
  const safeSrc = sanitizeImageSrc(src);
  if (!safeSrc) return null;
  return (
    <View style={styles.viewCard}>
      <Image src={safeSrc} style={styles.viewImage} />
      <Text style={[styles.metricLabel, { color: INK }]}>{label}</Text>
    </View>
  );
}

const roomLabel = (name) => {
  return String(name || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, c => c.toUpperCase());
};

const fitFont = (text, boxW, baseSize, minSize = 0) => {
  const chars = Math.max(1, String(text || "").length);
  const widthCap = (boxW * 0.92) / (chars * 0.55);
  const fs = Math.min(baseSize, widthCap);
  return minSize ? Math.max(minSize, fs) : fs;
};

function FloorPlanTopDown({ floor0, mep, mode = "architectural", project, height = 255, sharedBounds = null }) {
  let minX = Infinity, minZ = Infinity, maxX = -Infinity, maxZ = -Infinity;
  floor0.forEach(r => {
    const rx = r.x || 0;
    const rz = r.z || 0;
    const rw = r.width || 0;
    const rl = r.length || 0;
    if (rx < minX) minX = rx;
    if (rz < minZ) minZ = rz;
    if (rx + rw > maxX) maxX = rx + rw;
    if (rz + rl > maxZ) maxZ = rz + rl;
  });

  if (minX === Infinity) {
    minX = 0; minZ = 0; maxX = 10; maxZ = 10;
  }

  if (sharedBounds) {
    minX = sharedBounds.minX; minZ = sharedBounds.minZ;
    maxX = sharedBounds.maxX; maxZ = sharedBounds.maxZ;
  }

  const plotW = maxX - minX;
  const plotH = maxZ - minZ;
  if (plotW <= 0 || plotH <= 0) return <View style={{ width: "100%", height, backgroundColor: WHITE }} />;

  let pad = Math.max(plotW, plotH) * 0.12;
  if (mode === "site") pad = Math.max(plotW, plotH) * 0.4;
  const vbX = minX - pad;
  const vbY = minZ - pad;
  const vbW = plotW + pad * 2;
  const vbH = plotH + pad * 2;
  const vb = `${vbX} ${vbY} ${vbW} ${vbH}`;

  const labelSize = Math.max(plotW, plotH) * 0.035;
  const dimSize = labelSize * 0.7;

  return (
    <View style={{ width: "100%", height, backgroundColor: WHITE }}>
      <Svg viewBox={vb} width="100%" height="100%">
        
        {mode === "site" ? (
          <>
            <Rect x={minX - pad*0.7} y={minZ - pad*0.7} width={plotW + pad*1.4} height={plotH + pad*1.4} fill="#f1f5f9" stroke={INK} strokeWidth={0.8} strokeDasharray="5,5" />
            <Text x={minX + plotW/2} y={minZ - pad*0.8} style={{ fontSize: dimSize * 1.5, fill: INK, textAnchor: "middle", fontWeight: "bold" }}>PLOT BOUNDARY ({Math.round(project.plot?.width || 40)}' x {Math.round(project.plot?.length || 40)}')</Text>
            
            <Rect x={minX - pad*0.7} y={minZ + plotH + pad*0.7} width={plotW + pad*1.4} height={pad*0.5} fill="#e2e8f0" stroke={INK} strokeWidth={0.4} />
            <Text x={minX + plotW/2} y={minZ + plotH + pad*0.95} style={{ fontSize: dimSize * 1.5, fill: INK, textAnchor: "middle", fontWeight: "bold" }}>ACCESS ROAD</Text>
            
            <SvgLine x1={minX + plotW/2} y1={minZ - pad*0.7} x2={minX + plotW/2} y2={minZ} stroke={MUTED} strokeWidth={0.3} />
            <Text x={minX + plotW/2 + 1} y={minZ - pad*0.35} style={{ fontSize: dimSize, fill: MUTED }}>FRONT SETBACK ({(project.plot?.frontSetback || 3)}m)</Text>
            
            <SvgLine x1={minX - pad*0.7} y1={minZ + plotH/2} x2={minX} y2={minZ + plotH/2} stroke={MUTED} strokeWidth={0.3} />
            <Text x={minX - pad*0.35} y={minZ + plotH/2 - 1} style={{ fontSize: dimSize, fill: MUTED, textAnchor: "middle" }}>SIDE ({(project.plot?.sideSetback || 1.5)}m)</Text>
          </>
        ) : null}

        <Text x={maxX - 1.5} y={minZ + dimSize * 1.6} style={{ fontSize: dimSize * 1.1, fill: MUTED, textAnchor: "middle", fontWeight: "bold" }}>N↑</Text>

        {floor0.map(r => {
          const rx = r.x || 0;
          const rz = r.z || 0;
          const rw = r.width || 1;
          const rl = r.length || 1;
          const cx = rx + rw / 2;
          const cy = rz + rl / 2;
          const label = roomLabel(r.name || r.type || r.id);
          const dims = `${Math.round(rw)}' x ${Math.round(rl)}'`;

          const isMEP = mode === "electrical" || mode === "plumbing";
          
          // FIX 1: Pin labels to the top ONLY for furniture and dimensioned plans 
          // so the center stays clear for the icons/blue text.
          const topLabel = mode === "furniture" || mode === "dimensioned";
          const compact = /bath|toilet|powder|pooja|store|utility|wash/i.test(r.type || "");
          const baseLabel = (compact ? labelSize * 0.7 : labelSize) * (topLabel ? 0.8 : 1);
          const labelFont = isMEP ? labelSize * 0.45 : fitFont(label, rw, baseLabel);
          const dimFont = fitFont(dims, rw, dimSize);
          
          // FIX 2: Strictly disable gray dimensions in "dimensioned" mode to prevent double-text
          const showDims = mode === "architectural" && !topLabel && rl > (labelFont + dimFont) * 1.4 && rw > 4;
          
          const labelY = isMEP ? rz + 1
            : topLabel ? rz + labelFont * 1.5 
            : (showDims ? cy - dimFont * 0.3 : cy + labelFont * 0.35);

          return (
            <React.Fragment key={r.id}>
              <Rect x={rx} y={rz} width={rw} height={rl} fill={mode === "site" ? "#e2e8f0" : "#ffffff"} stroke={INK} strokeWidth={0.3} />

              {mode !== "site" && (
                <>
                  <Text x={isMEP ? rx + 0.5 : cx} y={labelY} style={{ fontSize: labelFont, fontWeight: 700, fill: isMEP ? MUTED : INK, textAnchor: isMEP ? "start" : "middle", opacity: isMEP ? 0.6 : 1 }}>{label}</Text>
                  
                  {/* Standard gray architectural dimensions */}
                  {showDims && <Text x={cx} y={cy + labelFont * 0.9} style={{ fontSize: dimFont, fill: MUTED, textAnchor: "middle" }}>{dims}</Text>}
                  
                  {/* Dimensioned plan blue text - spaced cleanly below the top label */}
                  {mode === "dimensioned" && rw > 4 && rl > 4 && (
                    <Text x={cx} y={labelY + dimFont + 2.0} style={{ fontSize: dimFont, fill: "#0ea5e9", textAnchor: "middle" }}>{dims}</Text>
                  )}
                </>
              )}
              {mode === "site" && r.name.toLowerCase().includes("living") && (
                <Text x={cx} y={cy} style={{ fontSize: dimSize * 1.5, fontWeight: 700, fill: INK, textAnchor: "middle" }}>HOUSE FOOTPRINT</Text>
              )}

              {/* Furniture layout - pushed down below the top label to prevent overlap */}
              {mode === "furniture" && (() => {
                const top = rz + rl * 0.4; // Starts at 40% down instead of 30%            
                const zoneH = rl * 0.55;
                const fLabel = Math.min(dimSize * 0.7, rw * 0.12);
                return (
                  <>
                    {r.type.includes("bedroom") && <Rect x={rx+rw*0.08} y={top} width={rw*0.4} height={zoneH*0.7} fill="#f1f5f9" stroke={MUTED} strokeWidth={0.2} />}
                    {r.type.includes("bedroom") && <Text x={rx+rw*0.28} y={top + zoneH*0.38} style={{ fontSize: fLabel, fill: MUTED, textAnchor: "middle" }}>BED</Text>}
                    
                    {r.type.includes("living") && <Rect x={rx+rw*0.5} y={top} width={rw*0.4} height={zoneH*0.4} fill="#f1f5f9" stroke={MUTED} strokeWidth={0.2} />}
                    {r.type.includes("living") && <Text x={rx+rw*0.7} y={top + zoneH*0.22} style={{ fontSize: fLabel, fill: MUTED, textAnchor: "middle" }}>SOFA</Text>}
                    
                    {r.type.includes("living") && <Rect x={rx+rw*0.08} y={top} width={rw*0.1} height={zoneH*0.5} fill="#f1f5f9" stroke={MUTED} strokeWidth={0.2} />}
                    {r.type.includes("living") && <Text x={rx+rw*0.13} y={top + zoneH*0.28} style={{ fontSize: fLabel, fill: MUTED, textAnchor: "middle" }}>TV</Text>}
                    
                    {r.type.includes("kitchen") && <Rect x={rx+rw*0.05} y={rz+rl*0.78} width={rw*0.9} height={rl*0.16} fill="#e2e8f0" stroke={MUTED} strokeWidth={0.2} />}
                    {r.type.includes("kitchen") && <Text x={rx+rw/2} y={rz+rl*0.87} style={{ fontSize: fLabel, fill: MUTED, textAnchor: "middle" }}>COUNTER</Text>}
                  </>
                );
              })()}
            </React.Fragment>
          );
        })}

        {mode === "dimensioned" && (
          <>
            <SvgLine x1={minX} y1={minZ - 2} x2={maxX} y2={minZ - 2} stroke="#0ea5e9" strokeWidth={0.3} />
            <Text x={minX + plotW/2} y={minZ - 2.5} style={{ fontSize: dimSize, fill: "#0ea5e9", textAnchor: "middle" }}>TOTAL: {Math.round(plotW)} ft</Text>
            <SvgLine x1={maxX + 2} y1={minZ} x2={maxX + 2} y2={maxZ} stroke="#0ea5e9" strokeWidth={0.3} />
            <Text x={maxX + 2.5} y={minZ + plotH/2} style={{ fontSize: dimSize, fill: "#0ea5e9" }}>TOTAL: {Math.round(plotH)} ft</Text>
          </>
        )}

        {/* MEP Overlay */}
        {mode === 'electrical' && (
          <React.Fragment>
            <SvgLine x1={minX - 2} y1={minZ + 2} x2={minX + 2} y2={minZ - 2} stroke="#ef4444" strokeWidth={0.8} />
            <SvgLine x1={minX - 2} y1={minZ - 2} x2={minX + 2} y2={minZ + 2} stroke="#ef4444" strokeWidth={0.8} />
            <Text x={minX + 3} y={minZ} style={{ fontSize: dimSize * 0.8, fill: "#ef4444", fontWeight: "bold" }}>MAIN ELEC DROP</Text>
          </React.Fragment>
        )}
        {mode === 'electrical' && floor0.map(r => {
          const nodes = r.mep_nodes || [];
          const paths = r.wiring_paths || [];
          return (
            <React.Fragment key={`elec-${r.id}`}>


              {nodes.map((node, i) => {
                if (node.type === "main_db") {
                  return (
                    <Rect key={`db-${i}`} x={node.x - 1.0} y={node.z - 1.0} width={2.0} height={2.0} fill="#ef4444" stroke="#000" strokeWidth={0.2} />
                  );
                } else if (node.type === "switchboard") {
                  return (
                    <Rect key={`sb-${i}`} x={node.x - 0.5} y={node.z - 0.5} width={1.0} height={1.0} fill="#64748b" stroke="#000" strokeWidth={0.2} />
                  );
                } else if (node.type.includes("ceiling_light") || node.type.includes("light")) {
                  return <Circle key={`l-${i}`} cx={node.x} cy={node.z} r={0.55} fill="#eab308" stroke="#000" strokeWidth={0.2} />;
                } else if (node.type === "fan") {
                  return <Path key={`f-${i}`} d={`M ${node.x-0.6} ${node.z} L ${node.x+0.6} ${node.z} M ${node.x} ${node.z-0.6} L ${node.x} ${node.z+0.6}`} stroke="#0f172a" strokeWidth={0.4} />;
                } else if (node.type.includes("socket")) {
                  return (
                    <React.Fragment key={`sock-${i}`}>
                      <Rect x={node.x - 0.4} y={node.z - 0.4} width={0.8} height={0.8} fill="#fff" stroke="#000" strokeWidth={0.2} />
                      <SvgLine x1={node.x - 0.4} y1={node.z} x2={node.x + 0.4} y2={node.z} stroke="#000" strokeWidth={0.2} />
                    </React.Fragment>
                  );
                } else if (node.type === "ac" || node.type === "geyser" || node.type === "oven") {
                  return <Rect key={`h-${i}`} x={node.x - 0.6} y={node.z - 0.6} width={1.2} height={1.2} fill="#f97316" stroke="#000" strokeWidth={0.2} />;
                }
                return null;
              })}
            </React.Fragment>
          );
        })}

        {mode === 'plumbing' && (
          <React.Fragment>
            <SvgLine x1={minX - 2} y1={maxZ - 2} x2={minX + 2} y2={maxZ + 2} stroke="#3b82f6" strokeWidth={0.8} />
            <SvgLine x1={minX - 2} y1={maxZ + 2} x2={minX + 2} y2={maxZ - 2} stroke="#3b82f6" strokeWidth={0.8} />
            <Text x={minX + 3} y={maxZ} style={{ fontSize: dimSize * 0.8, fill: "#3b82f6", fontWeight: "bold" }}>MAIN WATER SUPPLY</Text>
          </React.Fragment>
        )}
        {mode === 'plumbing' && floor0.map(r => {
          const nodes = r.mep_nodes || [];
          const paths = r.plumbing_paths || [];
          return (
            <React.Fragment key={`plum-${r.id}`}>


              {nodes.filter(n => n.type === "water_source" || n.type === "ug_tank" || n.type === "oh_tank" || n.type === "pump" || n.type === "manifold" || n.type.includes("sink") || n.type.includes("basin") || n.type === "wc" || n.type.includes("shower") || n.type.includes("drain")).map((node, i) => (
                  <React.Fragment key={`wn-${i}`}>
                    {node.type === "wc" ? (
                      <Rect x={node.x - 0.8} y={node.z - 1.0} width={1.6} height={2.0} fill="#fff" stroke="#0891b2" strokeWidth={0.3} />
                    ) : node.type.includes("sink") || node.type.includes("basin") ? (
                      <Circle cx={node.x} cy={node.z} r={0.8} fill="#fff" stroke="#0891b2" strokeWidth={0.3} />
                    ) : node.type.includes("shower") ? (
                      <Rect x={node.x - 1.0} y={node.z - 1.0} width={2.0} height={2.0} fill="none" stroke="#0891b2" strokeWidth={0.3} />
                    ) : node.type.includes("drain") ? (
                      <Circle cx={node.x} cy={node.z} r={0.5} fill="none" stroke="#78350f" strokeWidth={0.3} strokeDasharray="0.5,0.5" />
                    ) : (
                      <Circle cx={node.x} cy={node.z} r={1.2} fill="#06b6d4" stroke="#0891b2" strokeWidth={0.2} />
                    )}
                  </React.Fragment>
                ))}
            </React.Fragment>
          );
        })}

        {mode === 'structural' && project.structural_nodes?.map((node, i) => {
          if (node.type === "footing") {
            return <Rect key={`ft-${i}`} x={node.x - 1.5} y={node.z - 1.5} width={3.0} height={3.0} fill="none" stroke="#64748b" strokeWidth={0.3} strokeDasharray="1,1" />;
          } else if (node.type === "column") {
            return (
              <React.Fragment key={`col-${i}`}>
                <Rect x={node.x - 0.5} y={node.z - 0.5} width={1.0} height={1.0} fill="#000" />
                <Text x={node.x + 0.8} y={node.z} style={{ fontSize: dimSize * 0.6, fill: "#ef4444", fontWeight: "bold" }}>C{i+1}</Text>
              </React.Fragment>
            );
          }
          return null;
        })}

        {mode === 'structural' && project.structural_paths?.filter(p => p.type === "roof_beam").map((path, i) => {
          return (
            <React.Fragment key={`bm-${i}`}>
              <Path d={`M ${path.from.x} ${path.from.z} L ${path.from.x} ${path.to.z} L ${path.to.x} ${path.to.z}`} fill="none" stroke="#000" strokeWidth={0.6} />
              <Text x={(path.from.x + path.to.x)/2} y={(path.from.z + path.to.z)/2 - 0.5} style={{ fontSize: dimSize * 0.5, fill: "#2563eb", textAnchor: "middle" }}>B{i+1}</Text>
            </React.Fragment>
          );
        })}
      </Svg>
    </View>
  );
}

const floorCaption = { fontSize: 7.5, fontWeight: 700, letterSpacing: 0.8, color: WHITE, textAlign: "center", marginBottom: 3 };

function PdfLegend({ items }) {
  return (
    <View style={{ flexDirection: "row", flexWrap: "wrap", marginTop: 6, marginBottom: 4 }}>
      {items.map((it, i) => (
        <View key={i} style={{ flexDirection: "row", alignItems: "center", marginRight: 12, marginBottom: 2 }}>
          <View style={{
            width: 8, height: it.line ? 2 : 8,
            borderRadius: it.round ? 4 : 0,
            backgroundColor: it.color,
            borderWidth: it.border ? 0.5 : 0, borderColor: "#0f172a",
            marginRight: 3,
          }} />
          <Text style={{ fontSize: 7, color: "#334155" }}>{it.label}</Text>
        </View>
      ))}
    </View>
  );
}

const ELECTRICAL_LEGEND = [
  { color: "#eab308", line: true, label: "Lighting circuit" },
  { color: "#ef4444", line: true, label: "Power circuit" },
  { color: "#f97316", line: true, label: "Heavy load (AC/Geyser)" },
  { color: "#eab308", round: true, border: true, label: "Light point" },
  { color: "#64748b", border: true, label: "Switchboard" },
  { color: "#ef4444", border: true, label: "Main DB" },
  { color: "#ffffff", border: true, label: "Socket" },
  { color: "#f97316", border: true, label: "Heavy appliance" },
];

const PLUMBING_LEGEND = [
  { color: "#3b82f6", line: true, label: "Cold water" },
  { color: "#f97316", line: true, label: "Hot water" },
  { color: "#78350f", line: true, label: "Drain line" },
  { color: "#06b6d4", round: true, border: true, label: "Fixture (sink/basin)" },
  { color: "#ffffff", border: true, label: "WC" },
  { color: "#0891b2", border: true, label: "Tank / Source" },
];

const combinedBounds = (rooms) => {
  let minX = Infinity, minZ = Infinity, maxX = -Infinity, maxZ = -Infinity;
  rooms.forEach(r => {
    const rx = r.x || 0, rz = r.z || 0, rw = r.width || 0, rl = r.length || 0;
    if (rx < minX) minX = rx;
    if (rz < minZ) minZ = rz;
    if (rx + rw > maxX) maxX = rx + rw;
    if (rz + rl > maxZ) maxZ = rz + rl;
  });
  if (minX === Infinity) return null;
  return { minX, minZ, maxX, maxZ };
};

function VectorBlueprint({ project, mode = "architectural" }) {
  const rooms = project.rooms || [];
  const floor0 = rooms.filter(r => !r.isFloor1);
  const floor1 = rooms.filter(r => r.isFloor1);

  if (!floor0.length && !floor1.length) {
    return <View style={{ width: "100%", height: 255, backgroundColor: WHITE }} />;
  }

  if (!floor1.length) {
    return (
      <View style={{ width: "100%", height: 255 }}>
        <Text style={floorCaption}>GROUND FLOOR PLAN</Text>
        <FloorPlanTopDown floor0={floor0} mep={project.mep_data} mode={mode} project={project} height={240} />
      </View>
    );
  }

  const shared = combinedBounds([...floor0, ...floor1]);
  return (
    <View style={{ width: "100%", height: 255, flexDirection: "row", gap: 8 }}>
      <View style={{ flex: 1 }}>
        <Text style={floorCaption}>GROUND FLOOR PLAN</Text>
        <FloorPlanTopDown floor0={floor0} mep={project.mep_data} mode={mode} project={project} height={235} sharedBounds={shared} />
      </View>
      <View style={{ flex: 1 }}>
        <Text style={floorCaption}>FIRST FLOOR PLAN</Text>
        <FloorPlanTopDown floor0={floor1} mep={project.mep_data_f1} mode={mode} project={project} height={235} sharedBounds={shared} />
      </View>
    </View>
  );
}

export default function ArchitectReport({ project, snapshot }) {
  const rawShots = typeof snapshot === "string" ? { blueprint: snapshot } : snapshot || project.exportSnapshot || {};
  const shots = {
    blueprint: sanitizeImageSrc(rawShots.blueprint),
    perspective: sanitizeImageSrc(rawShots.perspective),
    front: sanitizeImageSrc(rawShots.front),
    side: sanitizeImageSrc(rawShots.side)
  };
  
  const rooms = project.rooms || [];
  const floor1 = rooms.filter(r => r.isFloor1);
  const actualFloors = floor1.length > 0 ? "GROUND + 1" : "GROUND ONLY";

  let actualMainDoors = 0, actualInternalRaw = 0, actualWindows = 0;
  rooms.forEach(r => {
    (r.doors || []).forEach(d => { if (d.is_main) actualMainDoors++; else actualInternalRaw++; });
    actualWindows += (r.windows || []).length;
  });
  const actualInternalDoors = Math.floor(actualInternalRaw / 2) || 1;
  if (actualMainDoors === 0) actualMainDoors = 1;

  const materialTotal = project.materials.reduce((sum, item) => sum + item.total, 0);
  const laborTotal = 1550000 + 1200000 + 1150000 + 250000;
  const subtotal = materialTotal + laborTotal;
  const contingency = subtotal * 0.07;
  const projectTotal = subtotal + contingency;

  const elecSchedule = rooms.map(r => {
    let lights = 0, fans = 0, sockets = 0, heavy = 0;
    (r.mep_nodes || []).forEach(n => {
      if (n.type.includes("light")) lights++;
      else if (n.type === "fan") fans++;
      else if (n.type.includes("socket")) sockets++;
      else if (n.type === "ac" || n.type === "geyser" || n.type === "oven") heavy++;
    });
    const watts = (lights * 15) + (fans * 60) + (sockets * 200) + (heavy * 2000);
    const amps = watts / 240;
    let fixtures = [];
    if (lights > 0) fixtures.push(`${lights}x LED`);
    if (fans > 0) fixtures.push(`${fans}x Fan`);
    if (sockets > 0) fixtures.push(`${sockets}x 15A Socket`);
    if (heavy > 0) fixtures.push(`${heavy}x 240V Heavy`);
    return { name: (r.isFloor1 ? "FF - " : "GF - ") + r.name, voltage: "240V", amps: amps.toFixed(1) + "A", fixtures: fixtures.join(", ") || "None", watts };
  });

  const totalWatts = elecSchedule.reduce((s, r) => s + r.watts, 0);
  const totalAmps = totalWatts / 240;
  const serviceRequired = totalAmps < 100 ? "100A" : (totalAmps < 150 ? "150A" : "200A");

  const totalLights = elecSchedule.reduce((s, r) => s + (parseInt(r.fixtures.match(/(\d+)x LED/)?.[1]) || 0), 0);
  const totalFans = elecSchedule.reduce((s, r) => s + (parseInt(r.fixtures.match(/(\d+)x Fan/)?.[1]) || 0), 0);
  const totalSockets = elecSchedule.reduce((s, r) => s + (parseInt(r.fixtures.match(/(\d+)x 15A Socket/)?.[1]) || 0), 0);
  
  let plumbCounter = 1;
  const plumbSchedule = rooms.filter(r => r.type.includes("bath") || r.type.includes("kitchen")).map(r => {
    let fixs = [];
    let gpm = 0;
    (r.mep_nodes || []).forEach(n => {
      if (n.type === "wc") { fixs.push("WC"); gpm += 2.5; }
      if (n.type.includes("sink") || n.type.includes("basin")) { fixs.push("SINK"); gpm += 1.5; }
      if (n.type.includes("shower")) { fixs.push("SHWR"); gpm += 2.0; }
    });
    let uniqueFixs = Array.from(new Set(fixs));
    let typeStr = uniqueFixs.length > 0 ? uniqueFixs.join(", ") : "None";
    return { name: (r.isFloor1 ? "FF - " : "GF - ") + r.name, itemId: `P-${plumbCounter++}`, type: typeStr, gpm: gpm.toFixed(1) };
  });

  const totalGpm = plumbSchedule.reduce((s, r) => s + parseFloat(r.gpm), 0);
  const mainLineSize = totalGpm < 10 ? "3/4 inch" : (totalGpm <= 20 ? "1 inch" : "1.25 inch");

  return (
    <Document title={`${project.name} Architect Export`} author="Home Vision AI">

      {/* A-001: COVER & FLOOR PLAN */}
      <Page size="A4" style={styles.page}>
        <Header project={project} sheet="A-001" />
        <Text style={styles.title}>{up(project.name)} - COVER & FLOOR PLAN</Text>
        <Text style={styles.subtitle}>
          {up(project.building.typology)} · {up(project.building.structure)} · {up(project.building.wallMaterial)}
          {project.plot && ` · PLOT AREA: ${Math.round(project.plot.width * project.plot.length).toLocaleString('en-IN')} SQFT / ${((project.plot.width * project.plot.length) / 43560).toFixed(3)} ACRES`}
          {"\n"}SOIL PROFILE: SBC 150 kN/m² · PLOT LIMITS: FRONT 3.0M, REAR 1.5M, SIDES 1.5M
        </Text>

        <View style={styles.blueprintPanel}>
          <BlueprintGrid />
          <VectorBlueprint project={project} mode="architectural" />
        </View>

        <View style={styles.metrics} wrap={false}>
          <View style={styles.metricCell}><Text style={styles.metricLabel}>EST. PROJECT COST</Text><Text style={styles.metricValue}>{formatRupees(projectTotal)}</Text></View>
          <View style={styles.metricCell}><Text style={styles.metricLabel}>STRUCTURAL SAFETY</Text><Text style={styles.metricValue}>{up(project.metrics.structuralSafety)}</Text></View>
          <View style={styles.metricCell}><Text style={styles.metricLabel}>CARBON ESTIMATE</Text><Text style={styles.metricValue}>{project.metrics.carbonKg.toLocaleString("en-IN")} KG</Text></View>
        </View>
        <Text style={styles.pageNum} render={({ pageNumber }) => `Page ${pageNumber}`} fixed />
      </Page>

      {/* A-002: SPECIFICATIONS & BOQ */}
      <Page size="A4" style={styles.page}>
        <Header project={project} sheet="A-002" />
        <Text style={styles.title}>SPECIFICATIONS + BOQ</Text>
        <View style={styles.specGrid}>
          <View style={styles.specPanel}>
            <SpecRow label="Plot" value={`${project.plot.width} ft x ${project.plot.length} ft`} />
            <SpecRow label="Built Area" value={`${project.metrics.areaSqft.toLocaleString("en-IN")} sq ft`} />
            <SpecRow label="Floors" value={actualFloors} />
            <SpecRow label="Ceiling" value={`${project.building.ceilingHeightFt} ft`} />
          </View>
          <View style={styles.specPanel}>
            <SpecRow label="Cost Tier" value={`${project.location.costTier} / ${project.location.multiplier}x`} />
            <SpecRow label="Foundation" value="SUGGESTED (REGIONAL DATA)" />
            <SpecRow label="Roof" value={project.building.roofing} />
            <SpecRow label="Validation" value="Engineering Review Required" />
          </View>
        </View>
        <Text style={styles.sectionTitle}>BILL OF MATERIALS</Text>
        <View style={styles.table}>
          <View style={[styles.tableRow, { backgroundColor: "#f1f5f9" }]} wrap={false}>
            <Text style={[styles.c1, styles.tableHead]}>MATERIAL</Text><Text style={[styles.c2, styles.tableHead]}>CATEGORY</Text><Text style={[styles.c3, styles.tableHead]}>QUANTITY</Text><Text style={[styles.c4, styles.tableHead]}>UNIT</Text><Text style={[styles.c5, styles.tableHead]}>TOTAL</Text>
          </View>
          {project.materials.map((item, idx) => {
             let qty = up(item.quantity);
             const nm = up(item.name);
             if (nm.includes("DOOR") && !nm.includes("WINDOW")) qty = `${actualMainDoors + actualInternalDoors} SETS`;
             if (nm.includes("WINDOW")) qty = `${actualWindows} SETS`;
             return (
              <View key={idx} style={styles.tableRow} wrap={false}>
                <Text style={styles.c1}>{nm}</Text><Text style={styles.c2}>{up(item.category)}</Text><Text style={styles.c3}>{qty}</Text><Text style={styles.c4}>{formatRupees(item.unitCost)}</Text><Text style={styles.c5}>{formatRupees(item.total)}</Text>
              </View>
             );
          })}
          <View style={[styles.tableRow, { backgroundColor: "#f1f5f9" }]} wrap={false}>
            <Text style={[styles.c1, styles.tableHead]}>RAW MATERIAL SUBTOTAL</Text><Text style={styles.c2}>BOM</Text><Text style={styles.c3}>-</Text><Text style={styles.c4}>-</Text><Text style={[styles.c5, styles.tableHead]}>{formatRupees(materialTotal)}</Text>
          </View>
          <View style={styles.tableRow} wrap={false}><Text style={styles.c1}>CIVIL LABOR COST</Text><Text style={styles.c2}>LABOR</Text><Text style={styles.c3}>-</Text><Text style={styles.c4}>-</Text><Text style={styles.c5}>{formatRupees(1550000)}</Text></View>
          <View style={styles.tableRow} wrap={false}><Text style={styles.c1}>MEP CONTRACTING</Text><Text style={styles.c2}>LABOR</Text><Text style={styles.c3}>-</Text><Text style={styles.c4}>-</Text><Text style={styles.c5}>{formatRupees(1200000)}</Text></View>
          <View style={styles.tableRow} wrap={false}><Text style={styles.c1}>FINISHES & INTERIOR LABOR</Text><Text style={styles.c2}>LABOR</Text><Text style={styles.c3}>-</Text><Text style={styles.c4}>-</Text><Text style={styles.c5}>{formatRupees(1150000)}</Text></View>
          <View style={styles.tableRow} wrap={false}><Text style={styles.c1}>PROFESSIONAL FEES & PERMITS</Text><Text style={styles.c2}>SERVICES</Text><Text style={styles.c3}>-</Text><Text style={styles.c4}>-</Text><Text style={styles.c5}>{formatRupees(250000)}</Text></View>
          <View style={styles.tableRow} wrap={false}><Text style={styles.c1}>CONTINGENCY RESERVE (7%)</Text><Text style={styles.c2}>RESERVE</Text><Text style={styles.c3}>-</Text><Text style={styles.c4}>-</Text><Text style={styles.c5}>{formatRupees(contingency)}</Text></View>
          <View style={[styles.tableRow, { backgroundColor: "#e2e8f0" }]} wrap={false}>
            <Text style={[styles.c1, styles.tableHead]}>PROJECT TOTAL ESTIMATE</Text><Text style={styles.c2}>TOTAL</Text><Text style={styles.c3}>-</Text><Text style={styles.c4}>-</Text><Text style={[styles.c5, styles.tableHead]}>{formatRupees(projectTotal)}</Text>
          </View>
        </View>
      </Page>

      {/* A-003: DIMENSIONED CONSTRUCTION PLAN */}
      <Page size="A4" style={styles.page}>
        <Header project={project} sheet="A-003" />
        <Text style={styles.title}>DIMENSIONED CONSTRUCTION PLAN</Text>
        <Text style={{ fontSize: 8, color: "#94a3b8", marginBottom: 5 }}>SHOWING OVERALL DIMENSIONS, WALL OFFSETS, DOOR/WINDOW LOCATIONS.</Text>
        <View style={styles.blueprintPanel}><VectorBlueprint project={project} mode="dimensioned" /></View>
        
        <Text style={styles.sectionTitle}>DOOR & WINDOW SCHEDULE</Text>
        <View style={styles.table}>
          <View style={[styles.tableRow, { backgroundColor: "#f1f5f9" }]} wrap={false}>
            <Text style={[styles.c1, styles.tableHead]}>TAG</Text><Text style={[styles.c2, styles.tableHead]}>TYPE</Text><Text style={[styles.c3, styles.tableHead]}>DIMENSIONS</Text><Text style={[styles.c4, styles.tableHead]}>MATERIAL</Text><Text style={[styles.c5, styles.tableHead]}>QTY</Text>
          </View>
          <View style={styles.tableRow} wrap={false}><Text style={styles.c1}>D1 (MAIN ENTRANCE)</Text><Text style={styles.c2}>DOOR</Text><Text style={styles.c3}>900 x 2100 mm</Text><Text style={styles.c4}>TEAK FLUSH</Text><Text style={styles.c5}>{actualMainDoors} SET</Text></View>
          <View style={styles.tableRow} wrap={false}><Text style={styles.c1}>D2 (INTERNAL)</Text><Text style={styles.c2}>DOOR</Text><Text style={styles.c3}>750 x 2100 mm</Text><Text style={styles.c4}>FLUSH DOOR</Text><Text style={styles.c5}>{actualInternalDoors} SETS</Text></View>
          <View style={styles.tableRow} wrap={false}><Text style={styles.c1}>W1 (ROOMS)</Text><Text style={styles.c2}>WINDOW</Text><Text style={styles.c3}>1500 x 1200 mm</Text><Text style={styles.c4}>UPVC GLAZED</Text><Text style={styles.c5}>{actualWindows} SETS</Text></View>
        </View>
      </Page>

      {/* A-004: SITE PLAN */}
      <Page size="A4" style={styles.page}>
        <Header project={project} sheet="A-004" />
        <Text style={styles.title}>SITE PLAN</Text>
        <Text style={{ fontSize: 8, color: "#94a3b8", marginBottom: 5 }}>SHOWING PLOT BOUNDARY, SETBACKS, NORTH ARROW AND ROAD ACCESS.</Text>
        <View style={styles.blueprintPanel}><VectorBlueprint project={project} mode="site" /></View>
      </Page>

      {/* A-005: STRUCTURAL LAYOUT */}
      <Page size="A4" style={styles.page}>
        <Header project={project} sheet="A-005" />
        <Text style={styles.title}>STRUCTURAL LAYOUT</Text>
        <Text style={{ fontSize: 8, color: "#ef4444", marginBottom: 5 }}>CONCEPTUAL STRUCTURAL GRID ONLY. ENGINEERING REVIEW REQUIRED. ■ Column ════ Beam</Text>
        <View style={styles.blueprintPanel}><VectorBlueprint project={project} mode="structural" /></View>
      </Page>

      {/* A-006: ELECTRICAL LAYOUT */}
      <Page size="A4" style={styles.page}>
        <Header project={project} sheet="A-006" />
        <Text style={styles.title}>ELECTRICAL LAYOUT & SCHEDULE</Text>
        <View style={styles.blueprintPanel}><VectorBlueprint project={project} mode="electrical" /></View>
        <PdfLegend items={ELECTRICAL_LEGEND} />
        <Text style={styles.sectionTitle}>ELECTRICAL SCHEDULE</Text>
        <View style={styles.table}>
          <View style={[styles.tableRow, { backgroundColor: "#f1f5f9" }]} wrap={false}>
            <Text style={[styles.c1, styles.tableHead]}>ROOM</Text><Text style={[styles.c2, styles.tableHead]}>VOLTAGE</Text><Text style={[styles.c3, styles.tableHead]}>AMPERAGE</Text><Text style={[styles.c4, styles.tableHead]}>FIXTURES</Text><Text style={[styles.c5, styles.tableHead]}>REMARKS</Text>
          </View>
          {elecSchedule.map((row, i) => (
            <View key={`es-${i}`} style={styles.tableRow} wrap={false}>
              <Text style={styles.c1}>{up(row.name)}</Text><Text style={styles.c2}>{row.voltage}</Text><Text style={styles.c3}>{row.amps}</Text><Text style={styles.c4}>{row.fixtures}</Text><Text style={styles.c5}>Standard Load</Text>
            </View>
          ))}
        </View>
        <View style={{ marginTop: 8, padding: 8, backgroundColor: "#f8fafc", border: `1 solid ${LINE}` }}>
          <Text style={{ fontSize: 9, fontWeight: "bold", marginBottom: 4 }}>PRELIMINARY LOAD SUMMARY</Text>
          <Text style={{ fontSize: 8, color: MUTED }}>Total Calculated Electrical Demand: {totalAmps.toFixed(1)} Amps</Text>
          <Text style={{ fontSize: 8, color: MUTED }}>Recommended Minimum Service: {serviceRequired} Main Panel</Text>
        </View>
      </Page>

      {/* A-007: PLUMBING LAYOUT */}
      <Page size="A4" style={styles.page}>
        <Header project={project} sheet="A-007" />
        <Text style={styles.title}>PLUMBING LAYOUT & SCHEDULE</Text>
        <Text style={{ fontSize: 8, color: "#94a3b8", marginBottom: 5 }}>WATER SOURCE: {up(project.indianOptions?.waterSource || "MUNICIPAL")} | STORAGE: {up(project.indianOptions?.waterStorage || "OH TANK")} | HEATER: {up(project.indianOptions?.hotWater || "GEYSER")}</Text>
        <View style={styles.blueprintPanel}><VectorBlueprint project={project} mode="plumbing" /></View>
        <PdfLegend items={PLUMBING_LEGEND} />
        <Text style={styles.sectionTitle}>PLUMBING SCHEDULE</Text>
        <View style={styles.table}>
          <View style={[styles.tableRow, { backgroundColor: "#f1f5f9" }]} wrap={false}>
            <Text style={[styles.c1, styles.tableHead]}>ROOM</Text><Text style={[styles.c2, styles.tableHead]}>ITEM ID</Text><Text style={[styles.c3, styles.tableHead]}>TYPE</Text><Text style={[styles.c4, styles.tableHead]}>PEAK GPM</Text><Text style={[styles.c5, styles.tableHead]}>REMARKS</Text>
          </View>
          {plumbSchedule.map((row, i) => (
            <View key={`ps-${i}`} style={styles.tableRow} wrap={false}>
              <Text style={styles.c1}>{up(row.name)}</Text><Text style={styles.c2}>{row.itemId}</Text><Text style={styles.c3}>{row.type}</Text><Text style={styles.c4}>{row.gpm}</Text><Text style={styles.c5}>Standard Flow</Text>
            </View>
          ))}
        </View>
        <View style={{ marginTop: 8, padding: 8, backgroundColor: "#f8fafc", border: `1 solid ${LINE}` }}>
          <Text style={{ fontSize: 9, fontWeight: "bold", marginBottom: 4 }}>PRELIMINARY LOAD SUMMARY</Text>
          <Text style={{ fontSize: 8, color: MUTED }}>Total Peak Water Demand: {totalGpm.toFixed(1)} GPM</Text>
          <Text style={{ fontSize: 8, color: MUTED }}>Recommended Minimum Supply Line: {mainLineSize}</Text>
        </View>
      </Page>

      {/* A-008: FURNITURE LAYOUT */}
      <Page size="A4" style={styles.page}>
        <Header project={project} sheet="A-008" />
        <Text style={styles.title}>FURNITURE LAYOUT</Text>
        <Text style={{ fontSize: 8, color: "#94a3b8", marginBottom: 5 }}>CONCEPTUAL USAGE LAYOUT FOR SPATIAL PLANNING.</Text>
        <View style={styles.blueprintPanel}><VectorBlueprint project={project} mode="furniture" /></View>
      </Page>

      {/* A-009: BUILDER SUMMARY */}
      <Page size="A4" style={styles.page}>
        <Header project={project} sheet="A-009" />
        <Text style={styles.title}>BUILDER SUMMARY</Text>
        <View style={styles.specGrid}>
          <View style={styles.specPanel}>
            <SpecRow label="Plot Area" value={`${Math.round(project.plot.width * project.plot.length)} SQFT`} />
            <SpecRow label="Built Area" value={`${project.metrics.areaSqft} SQFT`} />
            <SpecRow label="Floors" value={actualFloors} />
          </View>
          <View style={styles.specPanel}>
            <SpecRow label="Total Lights" value={totalLights} />
            <SpecRow label="Total Fans" value={totalFans} />
            <SpecRow label="Total Sockets" value={totalSockets} />
          </View>
        </View>
        <Text style={{ fontSize: 10, marginTop: 20, marginBottom: 10, fontWeight: "bold" }}>MAJOR QUANTITIES</Text>
        <View style={{ marginLeft: 10, gap: 5 }}>
          <Text style={{ fontSize: 9 }}>• CONCRETE: ~{Math.round(project.metrics.areaSqft * 0.15)} CU.M</Text>
                    <Text style={{ fontSize: 9 }}>• MASONRY: {up(project.building.wallMaterial)} ~{Math.round(project.metrics.areaSqft * 12)} BLOCKS</Text>
          <Text style={{ fontSize: 9 }}>• PAINTING AREA: ~{Math.round(project.metrics.areaSqft * 3.5)} SQFT</Text>
          <Text style={{ fontSize: 9 }}>• FLOORING TILES: ~{Math.round(project.metrics.areaSqft * 1.1)} SQFT</Text>
        </View>
      </Page>

              {/* A-010: MASTER PROJECT HANDOVER & SITE VERIFICATION */}
        <Page size="A4" style={styles.page}>
          <Header project={project} sheet="A-010" />
          <Text style={styles.title}>MASTER PROJECT HANDOVER & SITE VERIFICATION</Text>
          <View style={{ marginTop: 5, padding: 10 }}>
            
            <Text style={{ fontSize: 10, fontWeight: "bold", color: INK, marginBottom: 4 }}>1. Architectural Handover</Text>
            <View style={{ marginLeft: 5, marginBottom: 8, gap: 2 }}>
              <Text style={{ fontSize: 8, color: INK }}>✅ A.I. Generated:</Text>
              <Text style={{ fontSize: 7, color: MUTED }}>• Floor Plan & Room Layout  • Room, Door & Window Schedules  • Furniture Layout  • Site Plan & Setbacks  • Area Calculations  • Material Specifications  • Dimensioned Construction Plan</Text>
              <Text style={{ fontSize: 8, color: "#92400e", marginTop: 2 }}>⚠️ Architect Must Verify:</Text>
              <Text style={{ fontSize: 7, color: MUTED }}>• Local Building Code Compliance  • FAR / FSI Compliance  • Fire & Emergency Egress Requirements  • Ventilation & Natural Lighting Compliance  • Municipal Approval Requirements  • Accessibility Requirements</Text>
            </View>

            <Text style={{ fontSize: 10, fontWeight: "bold", color: INK, marginBottom: 4 }}>2. Structural Engineering Handover</Text>
            <View style={{ marginLeft: 5, marginBottom: 8, gap: 2 }}>
              <Text style={{ fontSize: 8, color: INK }}>✅ A.I. Generated:</Text>
              <Text style={{ fontSize: 7, color: MUTED }}>• Geometric Wall Intersections  • Suggested Column Placement Coordinates  • Conceptual Structural Grid  • Conceptual Beam Connectivity Layout  • Masonry Volume Estimates  • Concrete Volume Estimates</Text>
              <Text style={{ fontSize: 8, color: "#92400e", marginTop: 2 }}>⚠️ Structural Engineer Must Verify:</Text>
              <Text style={{ fontSize: 7, color: MUTED }}>• Safe Bearing Capacity (SBC) from Soil Investigation  • Foundation Type & Depth  • Column, Beam & Slab Design  • Dead, Live, Wind & Seismic Load Analysis  • Steel Quantities & Rebar Schedules  • Structural Stability</Text>
            </View>

            <Text style={{ fontSize: 10, fontWeight: "bold", color: INK, marginBottom: 4 }}>3. Electrical Engineering Handover</Text>
            <View style={{ marginLeft: 5, marginBottom: 8, gap: 2 }}>
              <Text style={{ fontSize: 8, color: INK }}>✅ A.I. Generated:</Text>
              <Text style={{ fontSize: 7, color: MUTED }}>• Fixture Counts (Lights, Fans, Sockets, Appliances)  • Suggested Switchboard Locations  • Conceptual Electrical Routing Layout  • Heavy Load Appliance Identification  • Electrical Schedule</Text>
              <Text style={{ fontSize: 8, color: "#92400e", marginTop: 2 }}>⚠️ Electrical Engineer Must Verify:</Text>
              <Text style={{ fontSize: 7, color: MUTED }}>• Total Connected Load (kW)  • Phase Balancing Requirements  • Wire Gauge Selection  • Earthing & Grounding Design  • MCB, RCCB & Distribution Board Sizing  • Electrical Safety Compliance</Text>
            </View>

            <Text style={{ fontSize: 10, fontWeight: "bold", color: INK, marginBottom: 4 }}>4. Plumbing Engineering Handover</Text>
            <View style={{ marginLeft: 5, marginBottom: 8, gap: 2 }}>
              <Text style={{ fontSize: 8, color: INK }}>✅ A.I. Generated:</Text>
              <Text style={{ fontSize: 7, color: MUTED }}>• Fixture Coordinates  • Wet Area Spatial Planning  • Water Source, Storage & Hot Water Preferences  • Plumbing Schedule</Text>
              <Text style={{ fontSize: 8, color: "#92400e", marginTop: 2 }}>⚠️ Plumbing Engineer Must Verify:</Text>
              <Text style={{ fontSize: 7, color: MUTED }}>• Flow Rate Requirements  • Pipe Diameter Selection  • Water Pressure Requirements  • Drainage Gradients & Slopes  • Pump Capacity & Horsepower  • Plumbing Code Compliance</Text>
            </View>

            <Text style={{ fontSize: 10, fontWeight: "bold", color: INK, marginBottom: 4 }}>5. Construction & Site Verification</Text>
            <View style={{ marginLeft: 5, marginBottom: 8, gap: 2 }}>
              <Text style={{ fontSize: 8, color: "#92400e" }}>⚠️ Contractor Must Verify:</Text>
              <Text style={{ fontSize: 7, color: MUTED }}>• Physical Site Dimensions  • Plot Boundary Accuracy  • Existing Drainage, Electrical, & Water Infrastructure  • Road Access Constraints  • Utility Clearances  • Site Conditions Prior to Construction</Text>
            </View>

            <View style={{ marginTop: 10, padding: 8, backgroundColor: "#fee2e2", borderLeft: "2 solid #ef4444", width: "100%", flexShrink: 1 }}>
              <Text style={{ fontSize: 9, color: "#b91c1c", fontWeight: "bold" }}>⚠️ LEGAL DISCLAIMER & REVIEW REQUIREMENTS</Text>
              <Text style={{ fontSize: 7, color: "#b91c1c", marginTop: 4 }}>This document is an AI-generated conceptual planning and design package.</Text>
              <Text style={{ fontSize: 7, color: "#b91c1c", marginTop: 2 }}>Detailed MEP routing is not included. The provided fixture locations and load calculations are preliminary and must be verified and routed by a licensed MEP engineer in accordance with local building codes.</Text>
              <Text style={{ fontSize: 7, color: "#b91c1c", marginTop: 2 }}>Home Vision AI does not certify structural safety, code compliance, engineering adequacy, or construction suitability.</Text>
            </View>
          </View>
          <Text style={styles.pageNum} render={({ pageNumber }) => `Page ${pageNumber}`} fixed />
        </Page>

    </Document>
  );
}