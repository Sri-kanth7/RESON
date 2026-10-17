---
name: Obsidian Telemetry
colors:
  surface: '#0c1322'
  surface-dim: '#0c1322'
  surface-bright: '#323949'
  surface-container-lowest: '#070e1d'
  surface-container-low: '#141b2b'
  surface-container: '#191f2f'
  surface-container-high: '#232a3a'
  surface-container-highest: '#2e3545'
  on-surface: '#dce2f7'
  on-surface-variant: '#bac9cc'
  inverse-surface: '#dce2f7'
  inverse-on-surface: '#293040'
  outline: '#849396'
  outline-variant: '#3b494c'
  surface-tint: '#00daf3'
  primary: '#c3f5ff'
  on-primary: '#00363d'
  primary-container: '#00e5ff'
  on-primary-container: '#00626e'
  inverse-primary: '#006875'
  secondary: '#d0bcff'
  on-secondary: '#3c0091'
  secondary-container: '#571bc1'
  on-secondary-container: '#c4abff'
  tertiary: '#a8ffd2'
  on-tertiary: '#003824'
  tertiary-container: '#5be9ad'
  on-tertiary-container: '#006645'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#9cf0ff'
  primary-fixed-dim: '#00daf3'
  on-primary-fixed: '#001f24'
  on-primary-fixed-variant: '#004f58'
  secondary-fixed: '#e9ddff'
  secondary-fixed-dim: '#d0bcff'
  on-secondary-fixed: '#23005c'
  on-secondary-fixed-variant: '#5516be'
  tertiary-fixed: '#6ffbbe'
  tertiary-fixed-dim: '#4edea3'
  on-tertiary-fixed: '#002113'
  on-tertiary-fixed-variant: '#005236'
  background: '#0c1322'
  on-background: '#dce2f7'
  surface-variant: '#2e3545'
typography:
  headline-xl:
    fontFamily: Inter
    fontSize: 2.25rem
    fontWeight: '700'
    lineHeight: 2.75rem
    letterSpacing: -0.025em
  headline-xl-mobile:
    fontFamily: Inter
    fontSize: 1.75rem
    fontWeight: '700'
    lineHeight: 2.25rem
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Inter
    fontSize: 1.5rem
    fontWeight: '600'
    lineHeight: 2rem
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Inter
    fontSize: 1.25rem
    fontWeight: '600'
    lineHeight: 1.75rem
    letterSpacing: -0.015em
  headline-sm:
    fontFamily: Inter
    fontSize: 1rem
    fontWeight: '600'
    lineHeight: 1.5rem
    letterSpacing: -0.01em
  body-lg:
    fontFamily: Inter
    fontSize: 1rem
    fontWeight: '400'
    lineHeight: 1.5rem
  body-md:
    fontFamily: Inter
    fontSize: 0.875rem
    fontWeight: '400'
    lineHeight: 1.375rem
  body-sm:
    fontFamily: Inter
    fontSize: 0.75rem
    fontWeight: '400'
    lineHeight: 1.125rem
  label-lg:
    fontFamily: JetBrains Mono
    fontSize: 0.875rem
    fontWeight: '500'
    lineHeight: 1.25rem
    letterSpacing: -0.01em
  label-md:
    fontFamily: JetBrains Mono
    fontSize: 0.75rem
    fontWeight: '500'
    lineHeight: 1rem
    letterSpacing: 0em
  label-sm:
    fontFamily: JetBrains Mono
    fontSize: 0.6875rem
    fontWeight: '400'
    lineHeight: 0.875rem
    letterSpacing: 0.02em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  gutter: 1rem
  margin: 1.5rem
  gutter-sm: 0.75rem
  margin-sm: 1rem
  gutter-lg: 1.5rem
  margin-lg: 2rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 0.75rem
  space-lg: 1rem
  space-xl: 1.5rem
---

## Brand & Style

This design system targets Site Reliability Engineers, DevOps architects, and enterprise incident response teams who diagnose mission-critical failures under intense operational pressure.

The aesthetic fuses **Precision Engineering Minimalism** with technical, high-density telemetry instrumentation. It avoids superficial design gimmicks—such as saturated neon blooms, excessive drop shadows, or generic conversational AI sparkle motifs—in favor of an unyielding, high-utility operational cockpit.

The emotional baseline must communicate:
- **Absolute Diagnostic Clarity:** Unambiguous information hierarchies where state, telemetry delta, and operational impact can be ingested instantly.
- **Calm Under Duress:** Deep midnight backgrounds reduce retinal fatigue during extended triage sessions, using focused, purposeful chroma only to direct attention to anomalies.
- **Instrumental Authority:** Sharp, structural layouts that convey enterprise reliability, deterministic data presentation, and surgical precision.

## Colors

The palette operates in a calibrated dark state, using stratified tonal values to represent telemetry depth and state progression without cognitive noise.

### Surface Architecture
- **Canvas Base (`#0B0F17`):** The foundational void, used exclusively for the global application viewport and backdrop behind modular panels.
- **Surface Elevation 1 (`#111827`):** Primary paneling, side navigations, and inactive grouping containers.
- **Surface Elevation 2 (`#161F30`):** Interactive cards, telemetry cells, inspect drawers, and metric tiles.
- **Surface Elevated Interactive (`#1E2B42`):** Hover states, active row selections, and focused container fills.
- **Subtle Surface Stroke (`#22324D`):** Primary structural perimeter border for bounding boxes, table lines, and container splits.

### Semantic Observability Tones
- **Intelligence Primary (`#00E5FF`):** Reserved for primary interactive controls, active analytical filters, timeline cursors, and AI-correlated causal paths. Secondary variant (`#00B4D8`) supports pressed states and active tab indicators.
- **Correlated Insight (`#8B5CF6`):** Signals causal graph linkages, cross-system dependency correlations, and automated hypothesis groups.
- **Critical Failure (`#EF4444`):** High-severity incidents, unhandled threshold breaches, downed nodes, and active alerts.
- **Operational Warning (`#F59E0B`):** Degradations, metric drift, rate limits, and flaky heuristics.
- **Healthy Nominal (`#10B981`):** Cleared alarms, operational health states, and resolved post-mortems.

### Foreground Hierarchy
- **Text High-Emphasis (`#F8FAFC`):** Metric values, primary headers, incident IDs.
- **Text Medium-Emphasis (`#94A3B8`):** Analytical metadata, axis markers, parameter keys, field labels.
- **Text Low-Emphasis (`#475569`):** Inactive structural icons, inactive timestamps, divider markers.

## Typography

The typography system separates UI control structures from operational data strings through a dual-type architecture:

- **Inter (Primary UI & Prose):** Used for navigation, layout hierarchies, incident writeups, dialogs, and structural UI interactions. Its neutral geometric balance ensures effortless scanning of complex, multi-pane layouts without drawing unnecessary attention to the letterforms.
- **JetBrains Mono (Technical Engine):** Applied strictly to tabular telemetry, latency metrics, RFC3339 timestamps, UUIDs, stack traces, signal hashes, and system logs. Monospaced characters ensure perfect tabular alignment across stacked time series and log aggregations.

### Rules of Engagement
- Never use decorative fonts, script styles, or high-contrast display serif typography.
- Metric values exceeding `24px` in dashboards must use tabular numerical features (`font-feature-settings: "tnum"`).
- Uppercase tracking is reserved exclusively for `label-sm` metadata tags and status indicators to maintain strict horizontal readability.

## Layout & Spacing

This design system uses a strict **12-column fluid grid** coupled with a disciplined **4px/8px incremental scale** tailored for high-density information displays.

### Layout Philosophy & Adaptability
- **Desktop (1440px and up):** 12-column fluid grid with `gutter-lg` (`1.5rem`) and `margin-lg` (`2rem`). Supports multi-pane diagnostic layouts, split telemetry panes (e.g., 4-column timeline / 8-column causal graph), and sticky inspection docks.
- **Laptop / Tablet (768px - 1439px):** 8-column fluid grid with `gutter` (`1rem`) and `margin` (`1.5rem`). Secondary side panels collapse into slide-over panels; telemetry matrices drop down to 2-column stacked layouts.
- **Mobile (below 768px):** 4-column fluid grid with `gutter-sm` (`0.75rem`) and `margin-sm` (`1rem`). Complex telemetry tables transform into stacked metric cards with horizontal scroll for log streams.

### Component Spacing Rhythms
- **Compact Data Groupings:** Use `space-xs` (`0.25rem`) between related key-value pairs (e.g., label title directly above telemetry value).
- **Control Grouping:** Use `space-sm` (`0.5rem`) between adjacent buttons, filters, and chips within toolbars.
- **Panel Internal Padding:** Use `space-md` (`0.75rem`) to `space-lg` (`1rem`) within metric cards and data containers to preserve high spatial density without visual crowding.
- **Section Breaks:** Use `space-xl` (`1.5rem`) to separate distinct narrative stages in an incident timeline.

## Elevation & Depth

Visual hierarchy is constructed via **tonal planar surfaces and surgical structural borders**, strictly eschewing heavy drop shadows, neon underglows, or fuzzy edge blurs.

### Planar Stacking Architecture
- **Layer 0 (Canvas):** `#0B0F17`. The root viewport.
- **Layer 1 (Modular Base):** `#111827`. Primary telemetry panels, logs streams, table containers. Border: `1px solid #22324D`.
- **Layer 2 (Contained Cells & Cards):** `#161F30`. Metric blocks, individual event frames, nested graph nodes. Border: `1px solid #22324D`.
- **Layer 3 (Transient Overlays):** `#1E2B42`. Context menus, tooltips, flyout inspector drawers, and dropdown select menus. Border: `1px solid #334766`.

### Shadows & Lighting
Shadows are minimal and purely functional:
- Drop shadows are barred from regular cards and dashboard tiles.
- Transient layers (dropdowns, popovers, modal inspect sheets) utilize a crisp ambient drop shadow: `box-shadow: 0 8px 24px -4px rgba(0, 0, 0, 0.65), 0 2px 6px -1px rgba(0, 0, 0, 0.4)`.
- Critical alert focus states use a sharp, non-blurred 1px outer ring in place of a diffuse colored glow: `outline: 1px solid #EF4444`.

## Shapes

The interface embraces a **Soft / Industrial Precision (`roundedness: 1`)** geometry. 

- **Standard UI Elements (`0.25rem` / `4px`):** Buttons, text input fields, search bars, chips, table row hover indicators, tabs, and metric tiles.
- **Grouping Containers & Cards (`0.5rem` / `8px`):** Top-level telemetry panels, evidence graph surfaces, and modal sheets.
- **Micro Radii (`2px`):** Applied exclusively to progress bar segments, inline sparkline markers, and timeline tick indicators.
- **Prohibited Shapes:** Fully circular pill buttons (`rounded-full`) are strictly forbidden, as they undermine the technical, instrument-grade cadence of the platform.

## Components

### Buttons
- **Primary Action:** Background `#00E5FF`, text `#0B0F17` (bold `Inter`), border `none`. Hover: `#00B4D8`. Active: `#0096B4`.
- **Secondary / Utility:** Background `#161F30`, text `#F8FAFC`, border `1px solid #22324D`. Hover: `#1E2B42`, border `#334766`.
- **Destructive:** Background `rgba(239, 68, 68, 0.12)`, text `#EF4444`, border `1px solid rgba(239, 68, 68, 0.3)`. Hover: `rgba(239, 68, 68, 0.2)`.
- **Dimension:** Fixed height `32px` for standard controls, `24px` for dense timeline and table-inline actions.

### Telemetry & Status Chips
- Height `20px`, padding `0 6px`, radius `4px`. Text style `label-sm` (`JetBrains Mono`).
- **Critical Alert:** Background `rgba(239, 68, 68, 0.12)`, text `#EF4444`, border `1px solid rgba(239, 68, 68, 0.35)`.
- **Warning:** Background `rgba(245, 158, 11, 0.12)`, text `#F59E0B`, border `1px solid rgba(245, 158, 11, 0.35)`.
- **Healthy:** Background `rgba(16, 185, 129, 0.12)`, text `#10B981`, border `1px solid rgba(16, 185, 129, 0.35)`.
- **Correlated Node:** Background `rgba(139, 92, 246, 0.12)`, text `#8B5CF6`, border `1px solid rgba(139, 92, 246, 0.35)`.

### Form Controls & Input Fields
- Background `#0B0F17`, text `#F8FAFC`, border `1px solid #22324D`, radius `4px`, height `32px`, font `label-md`.
- **Placeholder:** Text `#475569`.
- **Focus:** Border `1px solid #00E5FF`, zero box-shadow outline.
- **Checkboxes & Radios:** `14px` squares/circles with `1px solid #22324D`. Checked: fill `#00E5FF`, checkmark `#0B0F17`.

### Telemetry Cards & Metric Modules
- Container background `#161F30`, border `1px solid #22324D`, padding `space-md` (`0.75rem`).
- Header displays category in `label-sm` uppercase (`#94A3B8`), metric value in `headline-lg` (`#F8FAFC`, monospaced numerals), accompanied by an inline directional sparkline (`#00E5FF` or contextual status tone).

### Data Tables & Log Lists
- Striped alternating row backgrounds are prohibited. Default row: transparent; hover row: `#1E2B42`.
- Dividing border: `1px solid #22324D`. Header row uses `#111827`, typography `label-sm` uppercase, sticky positioning.
- Data columns containing timestamps, latencies, payloads, and IP addresses must use `JetBrains Mono`.

### Incident Evidence Graphs & Timelines
- Step-by-step incident nodes feature an active node point (`6px` solid circle) connected by a `1px` continuous rule (`#22324D`).
- Selected causal root nodes are highlighted with border `#00E5FF` and a subtle high-contrast fill (`#1E2B42`).