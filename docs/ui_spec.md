# UI Look & Feel Specification: Erie Iron

## 1. BRAND_INTENT

- **Industrial Precision with AI Intelligence**: The UI must convey the solidity of industrial automation (steel, iron, foundational infrastructure) combined with the sophistication of AI-driven business operations. Think "autonomous factory for digital businesses."
- **Dashboard for Oversight, Not Micromanagement**: Human users are governors and overseers, not operators. The interface prioritizes high-level metrics, strategic decisions, and exception handling over granular task management.
- **Information-Rich for Multi-Business Operations**: The platform manages multiple autonomous businesses simultaneously. The UI must present dense operational data (business health, agent activity, infrastructure status, deployment logs) without overwhelming the user.
- **Technical Credibility**: This is infrastructure-as-code and AI-as-a-service. The design must signal competence to technical audiences: developers, DevOps engineers, and technical founders.
- **Dark-First, Always-On Aesthetic**: Operations run 24/7. The UI uses a dark theme optimized for extended monitoring sessions, reduced eye strain, and a "command center" feel.
- **AWS-Native Transparency**: Infrastructure deployment, CloudFormation stacks, ECS tasks, and AWS resource monitoring are first-class citizens. The UI makes AWS operations visible and debuggable.

---

## 2. DESIGN_PRINCIPLES

1. **Bootstrap 5.3.3 Foundation**: All spacing, grid, and component patterns use Bootstrap utilities and components. Custom components extend Bootstrap patterns, never replace them.
2. **Dark Theme as Default**: Light backgrounds appear only in isolated contexts (code displays, architecture diagrams). The primary experience is dark mode with carefully calibrated contrast.
3. **Hierarchy Through Typography and Space**: Font weight, size, letter-spacing, and vertical rhythm establish information hierarchy. Color is reserved for semantic state (success, danger, warning), business status, and interactive elements.
4. **Glassomorphic Depth**: Cards, panels, and major sections use subtle gradients, backdrop blur effects, and layered shadows to create depth without garish decoration.
5. **Consistent Spacing Scale**: Use Bootstrap's 0-5 spacing scale exclusively. Comfortable spacing (3-4) for primary content, tighter spacing (2-3) for data-heavy dashboards and tables.
6. **Progressive Disclosure**: Show critical information first (business health, active deployments, recent agent actions). Use tabs, collapsible sections, and detail views to surface logs, configuration, and historical data.
7. **Immediate State Feedback**: Agent actions, infrastructure changes, and deployment status must show real-time updates. Loading states, progress indicators, and status badges are mandatory.

---

## 3. DESIGN_TOKENS

### Colors

```scss
// Brand & Primary
$primary: #3B82F6;  // Bright blue - primary actions, active states
$cast-iron: #2a2a1e;  // Dark charcoal - brand identity color

// Semantic
$secondary: #6c757d;  // Bootstrap gray
$success: #2a9d4a;  // Green - healthy states
$info: #06B6D4;  // Modern teal
$warning: #f26b00;  // Tangerine - warnings
$danger: #e63946;  // Grapefruit red - errors, failures
$light: #EAEAEA;  // Off-white for text on dark
$dark: #1E1E2E;  // Near-black slate

// Business Status (Custom theme colors)
$business-idea: #9333EA;  // Purple - concept/idea phase
$business-active: #059669;  // Emerald green - active operations
$business-paused: #D97706;  // Amber - paused/on-hold
$business-shutdown: #DC2626;  // Red - shutdown businesses

// Grayscale
$gray-100 through $gray-900  // Bootstrap defaults with custom 700-900

// Backgrounds
$body-bg: #2A2A38;  // Main application background
$nav-bg: #121212;  // Navigation bar background
$sidebar-bg: #1E1E2E;  // Sidebar background
$main-content-bg: #2A2A38;  // Main content area

// Text
$body-color: #EAEAEA;  // Default text (light on dark)
$white: #fdfaf6;  // Pure white for high contrast
$black: $cast-iron;  // Dark text for light backgrounds
$text-muted-color: #9CA3AF;  // Muted/secondary text
$sidebar-text: #9CA3AF;  // Sidebar default text

// Links
$link-color: $primary;
$link-hover-color: #60A5FA;  // Lighter blue on hover

// Navigation
$sidebar-hover-bg: #2A2A38;
$sidebar-hover-text: #EAEAEA;
$sidebar-active-bg: #3B82F6;
$sidebar-active-text: #EAEAEA;

// Dividers
$divider-color: #374151;
$table-border-color: $gray-300;
```

**Bootstrap Utility Mapping:**
- `bg-primary` → `$primary` (#3B82F6)
- `bg-dark` → `$dark` (#1E1E2E)
- `bg-light` → `$light` (#EAEAEA) - use sparingly
- `text-primary` → inherits body color (#EAEAEA on dark)
- `text-muted` → `$text-muted-color` (#9CA3AF)
- `bg-idea`, `bg-active`, `bg-paused`, `bg-shutdown` → business status colors

### Spacing

Use Bootstrap's spacing scale exclusively:

| Token | Pixels | Usage |
|-------|--------|-------|
| `0` | 0px | Remove spacing |
| `1` | 0.25rem (4px) | Tight inline elements, icon gaps |
| `2` | 0.5rem (8px) | Compact lists, badge groups |
| `3` | 1rem (16px) | Default card/section padding, vertical rhythm |
| `4` | 1.5rem (24px) | Section spacing, comfortable padding |
| `5` | 3rem (48px) | Major section dividers, page headers |

**Practical Application:**
- Card padding: `p-3` or `p-4`
- Section gaps: `gap-2` (1.5rem default), `gap-3` for spacious layouts
- Vertical rhythm between sections: `mb-4` or `mb-5`

### Radius

| Token | Value | Usage |
|-------|-------|-------|
| `rounded-0` | 0 | Tables, strict grids |
| `rounded-1` | 0.25rem | Small buttons, tight UI |
| `rounded-2` | 0.375rem | Default inputs, compact cards |
| `rounded-3` | 0.5rem | Standard cards, modals |
| Custom: `1rem` | 1rem | Nested sections within cards |
| Custom: `1.25rem` | 1.25rem | Major sections |
| Custom: `1.5rem` | 1.5rem | Overview cards, hero sections |
| `rounded-pill` | 50rem | Badges, chips, status pills |

### Shadows

```scss
// Glassomorphic layering with primary color hints
box-shadow: 0 18px 30px rgba($primary, 0.15);  // Top nav
box-shadow: 0 30px 45px rgba($nav-bg, 0.55);  // Overview cards (large depth)
box-shadow: 0 22px 38px rgba($nav-bg, 0.45);  // Sidebar
box-shadow: 0 10px 20px rgba($primary, 0.25);  // Active sidebar items, hover effects
box-shadow: 0 2px 12px rgba($primary, 0.25);  // Meta cards, elevated panels
box-shadow: inset 0 0 0 1px rgba($primary, 0.05);  // Subtle inset borders
box-shadow: inset 0 0 0 1px rgba($primary, 0.4), 0 10px 20px rgba($primary, 0.25);  // Active state layering
```

**Shadow Guidelines:**
- Ambient shadows use `$nav-bg` with 45-55% opacity for depth
- Interactive/active shadows use `$primary` with 15-25% opacity
- Inset shadows for subtle borders: 1px, 5-10% opacity

### Typography

**Font Family:**
```scss
$base-font: 'Inter', 'SF Pro Text', 'Helvetica Neue', Arial, sans-serif;
--font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
```

**Scale:**

| Class/Pattern | Size | Weight | Letter-Spacing | Usage |
|---------------|------|--------|----------------|-------|
| Overview title | `clamp(2rem, 2.8vw, 3rem)` | 700 | 0 | Hero headers (Business/Initiative) |
| `fs-1` | 2.5rem | 700 | 0 | Page-level headers |
| `fs-2` | 2rem | 600 | 0 | Major section headers |
| `.card h1` | 2.125rem | default | 0 | Card-level primary headers |
| `fs-3` | 1.75rem | 600 | 0 | Subsection headers |
| Section title | 1.15rem | default | 0.06em | Uppercase section labels |
| `fs-4` | 1.5rem | 600 | 0 | Card titles |
| Erie title large | 1.5rem | 600 | 0 | Component-level titles |
| Erie title | 1.125rem | 600 | 0 | Standard component titles |
| Overview summary | 1.05rem | default | 0 | Lead paragraph text |
| `fs-5` | 1.25rem | 500 | 0 | Body emphasis |
| `fs-6` / default | 1rem | 400 | 0 | Body copy |
| Erie meta value | 0.9rem | default | 0 | Metadata display |
| Erie subtitle | 0.9rem | default | 0 | Subtitles, secondary info |
| Chip/badge | 0.85rem | default | 0.04em | Status chips, small labels |
| Erie subtitle section | 0.85rem | default | 0.05em | Section metadata |
| `.small` | 0.875rem | 400 | 0 | Small text, metadata |
| Erie label | 0.75rem | default | 0.08em | Uppercase micro-labels |
| Eyebrow | 0.75rem | default | 0.12em | Uppercase eyebrow text |
| Erie label meta | 0.7rem | default | 0.06em | Smallest metadata labels |

**Weights:**
- `fw-normal` (400) – body copy
- `fw-medium` (500) – emphasis
- `fw-semibold` (600) – headers, important labels
- `fw-bold` (700) – hero text, primary headers

**Line Height:**
- `lh-1` (1.0) – tight, for badges/chips
- `lh-sm` (1.25) – compact lists
- `lh-base` (1.5) – body copy default
- Custom: `1.6` – message content, readable paragraphs
- Custom: `1.7` – overview summaries, relaxed reading
- `lh-lg` (2.0) – spacious headings

**Text Transforms & Letter-Spacing:**
- Uppercase + `letter-spacing: 0.08em` or `0.12em` for labels, eyebrows, section titles
- Uppercase + `letter-spacing: 0.04em` or `0.05em` for chips, badges, metadata
- Normal case + `letter-spacing: 0.015em` for sidebar labels (subtle refinement)

---

## 4. BOOTSTRAP_UTILITY_GUIDELINES

### DO:
- Use `p-{0-5}`, `m-{0-5}`, `px-`, `py-`, `mt-`, `mb-`, `gap-{0-5}` for all spacing.
- Use `d-flex`, `flex-column`, `flex-row`, `justify-content-*`, `align-items-*`, `flex-wrap` for layout.
- Use `text-start`, `text-center`, `text-end` for alignment.
- Use `fw-*`, `fs-*`, `lh-*` for typography.
- Use `rounded-{0-3}`, `rounded-pill` for standard radius; custom `border-radius` in component SCSS for larger values.
- Use `border`, `border-{top|bottom|start|end}`, `border-{0-5}` for borders.
- Use `bg-primary`, `bg-dark`, `bg-light`, `text-primary`, `text-muted`, `text-white` with custom token variables.
- Use `btn`, `btn-primary`, `btn-secondary`, `btn-outline-*` as base button classes.
- Use `form-control`, `form-select`, `form-check` for inputs.
- Use `col-{breakpoint}-{width}` for responsive grids.
- Use `card`, `card-body`, `card-header` for card structures.
- Use `list-group`, `list-group-item` for navigation lists.
- Use `badge`, `bg-{color}`, `rounded-pill` for status indicators.
- Use `table`, `table-sm`, `table-hover`, `table-striped` for data tables.

### DO NOT:
- Do not use inline `style="margin: 17px"` or arbitrary pixel values outside component SCSS.
- Do not create custom spacing classes like `.gap-custom-23`.
- Do not use CSS Grid unless Bootstrap's grid utilities are insufficient.
- Do not apply `float` or legacy positioning; use Flexbox.
- Do not define new color utility classes outside the Sass token system.
- Do not use `!important` except for rare override scenarios documented in comments (e.g., sidebar collapse states).
- Do not use light backgrounds for primary content areas (reserved for code/diagrams).

---

## 5. INTERACTION_RULES

### Hover
- **Buttons**: Background color transitions to hover state via Bootstrap classes. Buttons with custom styles darken by 10% or apply `rgba($primary, 0.X)` overlays.
- **Cards/Panels (clickable)**: Add subtle lift with `transform: translateY(-2px)` and shadow transition to `shadow-md` equivalent.
- **Links**: Transition color to `$link-hover-color` (#60A5FA) with `transition: color 0.2s ease`. Underline appears on hover for standalone links.
- **Sidebar Items**: Background transitions to `$sidebar-hover-bg` (#2A2A38), text to `$sidebar-hover-text` (#EAEAEA) with `transition: all 0.2s ease`.
- **Interactive Icons**: Scale to 1.05x with `transition: transform 0.15s ease`.

### Active
- **Buttons**: Apply Bootstrap `.active` state or custom active styles (darker background, subtle inset).
- **Sidebar Items**: Background becomes `linear-gradient(90deg, rgba($primary, 0.9), rgba($primary, 0.58))`, layered shadow `inset 0 0 0 1px rgba($primary, 0.4), 0 10px 20px rgba($primary, 0.25)`, color `$sidebar-active-text` (#EAEAEA).
- **Input Focus**: Blue ring via Bootstrap focus utilities: `box-shadow: 0 0 0 0.25rem rgba(13, 110, 253, 0.25)` or `rgba($primary, 0.25)`.

### Focus
- All interactive elements must show visible focus outline.
- Use Bootstrap's default `:focus-visible` pattern: `outline: 2px solid $primary; outline-offset: 2px;`.
- Do not remove default focus styles without accessible replacement.

### Disabled
- Buttons: `opacity: 0.5`, `cursor: not-allowed`, class `.disabled`, attribute `aria-disabled="true"`.
- Sidebar items: `color: rgba($sidebar-text, 0.45)`, no hover effects.
- Inputs: `background-color: $gray-200`, `cursor: not-allowed`.

### Motion
- **Transitions**: Use `transition: all 0.15s ease` or `0.2s ease` for hover/active states.
- **Animations**:
  - Fade-in messages: `opacity 0 → 1`, `translateY(10px) → 0`, duration `0.3s ease-in`.
  - Slide-in proposals: `opacity 0 → 1`, `translateX(-20px) → 0`, duration `0.4s ease-out`.
  - Loading spinners: `rotate(360deg)`, duration `1s linear infinite`.
- **Modals**: Use Bootstrap's `.fade` and `.show` classes.
- **Maximum duration**: 0.4s for animations, preferably 0.15-0.3s.

### Density
- **Comfortable (Default)**: `p-3` or `p-4` on cards, `gap-2` or `gap-3` between sections, `mb-4` between major blocks.
- **Compact (Dashboards, Tables)**: `p-2` on cards, `gap-1` or `gap-2`, `mb-3` between sections. Use `table-sm` for data tables.

---

## 6. COMPONENT_STYLES

### Buttons

**Primary Button:**
```html
<button class="btn btn-primary px-4 py-2 fw-semibold">
  Deploy Stack
</button>
```
- Background: `$primary` (#3B82F6)
- Text: white, `fw-semibold`
- Hover: Bootstrap hover state (darker blue)
- Padding: `py-2 px-4` (comfortable) or `py-1 px-2` (compact)
- Radius: Bootstrap default (`rounded-2` equivalent)

**Secondary Button:**
```html
<button class="btn btn-outline-secondary px-4 py-2">
  View Logs
</button>
```
- Border: `$secondary`
- Text: `$secondary`
- Hover: background `$secondary`, text white

**Text/Link Button:**
```html
<button class="btn btn-link text-muted p-0">
  Show Details
</button>
```
- No border, no background
- Hover: underline, color shift to `$link-hover-color`

**Disabled State:**
```html
<button class="btn btn-primary disabled" aria-disabled="true">
  Processing...
</button>
```
- `opacity: 0.5`
- `cursor: not-allowed`

---

### Input Fields

**Text Input:**
```html
<input type="text" class="form-control py-2 px-3" placeholder="Business name...">
```
- Border: `1px solid` Bootstrap default
- Radius: `rounded-2`
- Padding: `py-2 px-3`
- Focus: blue ring (Bootstrap default)

**Textarea:**
```html
<textarea class="form-control py-2 px-3" rows="4"></textarea>
```
- Same as text input

**Select:**
```html
<select class="form-select py-2 px-3">
  <option>All Businesses</option>
</select>
```

**Error State:**
- Add `.is-invalid` class
- Border: `$danger`
- Show `.invalid-feedback` div below

---

### Cards / Panels

**Overview Card (Business/Initiative/Task):**
```scss
.business-overview,
.initiative-overview,
.task-overview,
.iteration-overview {
  display: flex;
  flex-direction: column;
  gap: 2rem;
  padding: 2.5rem;
  background: radial-gradient(circle at top left, rgba($primary, 0.22), rgba($nav-bg, 0.94));
  border-radius: 1.5rem;
  border: 1px solid rgba($primary, 0.12);
  box-shadow: 0 30px 45px rgba($nav-bg, 0.55);
}
```
- **Structure**: Header (eyebrow, title, summary, chips) + Meta (status, dates) + Sections (nested content)
- **Eyebrow**: `font-size: 0.75rem; letter-spacing: 0.12em; text-transform: uppercase; color: rgba($light, 0.65);`
- **Title**: `clamp(2rem, 2.8vw, 3rem); font-weight: 700; color: $light;`
- **Summary**: `font-size: 1.05rem; line-height: 1.7; color: rgba($light, 0.82);`
- **Chips**: `padding: 0.45rem 0.9rem; border-radius: 999px; font-size: 0.85rem; letter-spacing: 0.04em; border: 1px solid rgba($primary, 0.3); background: rgba($nav-bg, 0.65);`
- **Meta**: `padding: 1.25rem 1.5rem; border-radius: 1rem; background: linear-gradient(180deg, rgba($primary, 0.25), rgba($primary, 0.08)); box-shadow: 0 2px 12px rgba($primary, 0.25);`
- **Sections**: `padding: 2rem; border-radius: 1.25rem; border: 1px solid rgba($primary, 0.1); background: rgba($nav-bg, 0.85);`

**Standard Card:**
```html
<div class="card bg-dark border-primary border-opacity-10 rounded-3 p-3 mb-3">
  <div class="card-body">
    <h5 class="card-title">Recent Activity</h5>
    <p class="card-text text-muted">...</p>
  </div>
</div>
```
- Background: `bg-dark` or `bg-body`
- Border: subtle primary tint `border-primary border-opacity-10`
- Radius: `rounded-3`

**Hover Behavior (Clickable Cards):**
- Custom class `.hover-lift` or inline transitions
- `transition: transform 0.15s, box-shadow 0.15s;`
- On hover: `transform: translateY(-2px); box-shadow: 0 10px 20px rgba($primary, 0.25);`

---

### Tables

**Data Table:**
```html
<table class="table table-sm table-hover align-middle">
  <thead class="text-uppercase">
    <tr>
      <th class="fw-semibold text-muted small">Task</th>
      <th class="fw-semibold text-muted small">Status</th>
      <th class="fw-semibold text-muted small">Agent</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td class="small">Deploy CloudFormation stack</td>
      <td><span class="badge bg-success rounded-pill small">Completed</span></td>
      <td class="small">self_driving_coder</td>
    </tr>
  </tbody>
</table>
```
- Use `table-sm` for compact rows
- Header: `text-uppercase`, `fw-semibold`, `text-muted`, `small`
- Hover: Bootstrap's `table-hover`
- Striping: `table-striped` with custom `$table-striped-bg: rgba($primary, 0.05);`
- Active row: `$table-active-bg: rgba($primary, 0.1);`
- Borders: `$table-border-color: $gray-300;`

**No-wrap headers:**
```scss
table thead th {
  white-space: nowrap;
}
```

---

### Badges / Status Indicators

**Status Badge:**
```html
<span class="badge bg-success rounded-pill small">Active</span>
<span class="badge bg-warning rounded-pill small">Paused</span>
<span class="badge bg-danger rounded-pill small">Failed</span>
<span class="badge bg-idea rounded-pill small">Idea</span>
```
- Always `rounded-pill`
- Font size: `small` (0.875rem) or `0.85rem`
- Custom business status: `bg-idea`, `bg-active`, `bg-paused`, `bg-shutdown`

**Chip (Overview/Metadata):**
```scss
.business-overview__chip {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.45rem 0.9rem;
  border-radius: 999px;
  font-size: 0.85rem;
  letter-spacing: 0.04em;
  border: 1px solid rgba($primary, 0.3);
  text-transform: uppercase;
  color: rgba($light, 0.85);
  background: rgba($nav-bg, 0.65);
}
```

---

### Navigation

**Top Nav:**
```scss
#top_nav {
  display: flex;
  align-items: center;
  min-height: 4.25rem;
  background: linear-gradient(135deg, rgba($primary, 0.35), rgba($nav-bg, 0.95));
  border-bottom: 1px solid rgba($primary, 0.18);
  box-shadow: 0 18px 30px rgba($primary, 0.15);
  backdrop-filter: blur(18px);
  padding: 0;
  z-index: 10;
}
```
- **Logo**: `height: 2rem; width: 2rem; filter: drop-shadow(0 6px 12px rgba($primary, 0.25));`
- **Brand**: `color: $light; font-weight: 600; letter-spacing: 0.04em;`
- **Breadcrumb**: Divider `"|"`, items `font-size: 0.9rem; color: rgba($light, 0.85);`, hover `color: lighten($primary, 15%);`

**Sidebar:**
```scss
.sidebar-modern {
  background: linear-gradient(180deg, rgba($sidebar-bg, 0.92), rgba($nav-bg, 0.88));
  border-right: 1px solid rgba($primary, 0.12);
  box-shadow: 0 22px 38px rgba($nav-bg, 0.45);
  padding: 1.5rem 0;
}
```
- **Title**: `font-size: 1rem; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: rgba($light, 0.75);`
- **Items**: `padding: 0.5rem 2rem; transition: all 0.2s ease;`
- **Hover**: `background: rgba($primary, 0.16); color: $light;`
- **Active**: `background: linear-gradient(90deg, rgba($primary, 0.9), rgba($primary, 0.58)); box-shadow: inset 0 0 0 1px rgba($primary, 0.4), 0 10px 20px rgba($primary, 0.25);`
- **Disabled**: `background: transparent; color: rgba($sidebar-text, 0.45);`

**Horizontal Tabs:**
```scss
.horizontal-tabs .nav-link {
  border: none;
  border-bottom: 2px solid transparent;
  background: none;
  color: rgba($light, 0.7);
  padding: 0.75rem 1.5rem;
  transition: all 0.2s ease;
  font-size: 0.9rem;
  font-weight: 500;
}
.horizontal-tabs .nav-link:hover {
  color: rgba($light, 0.9);
  border-bottom-color: rgba($primary, 0.5);
  background: rgba($primary, 0.1);
}
.horizontal-tabs .nav-link.active {
  color: $light;
  border-bottom-color: $primary;
  background: rgba($primary, 0.15);
}
```

---

### Code Display

**Code Container:**
```scss
.code-container {
  padding: 1rem;
  background-color: $gray-100 !important;  // Light background
  color: $dark !important;
  code {
    color: $dark !important;
  }
}
```
- Light background for readability
- Monospace font: `ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace`

**Pre/Log Output:**
```scss
.pre {
  white-space: pre-wrap;
  word-wrap: break-word;
  overflow-wrap: anywhere;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  tab-size: 4;
  overflow: auto;
  margin: 1em 0;
  font-size: 0.75rem;
}

.pre_log_output {
  max-height: 400px;
  overflow-y: auto;
}
```

**Syntax Highlighting:**
```scss
pre .highlight {
  .s1, .s2 {  // Strings
    color: darkcyan !important;
  }
  .c1 {  // Comments
    color: $gray-600 !important;
  }
  .nf {  // Functions
    font-weight: bold;
    color: cornflowerblue !important;
  }
}
```

---

### Conversations / Messaging

**Message Container:**
```scss
.conversation-message {
  margin-bottom: 1.25rem;
  padding: 1rem;
  border-radius: 0.5rem;
  animation: fadeIn 0.3s ease-in;
}

.conversation-message.message-user {
  background-color: #e3f2fd;  // Light blue
  margin-left: 3rem;
  border-left: 3px solid #2196f3;
}

.conversation-message.message-assistant {
  background-color: #f8f9fa;  // Light gray
  margin-right: 3rem;
  border-left: 3px solid #4caf50;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
}
```
- **Role Label**: `font-weight: 600; font-size: 0.875rem; text-transform: uppercase; letter-spacing: 0.5px;`
- **Content**: `line-height: 1.6; color: #212529;`
- **Code in messages**: `background-color: #e9ecef; padding: 0.2rem 0.4rem; border-radius: 0.25rem; font-family: monospace; font-size: 0.875em;`

**Change Proposal:**
```scss
.change-proposal {
  margin: 1.25rem 0;
  padding: 1.25rem;
  border: 2px solid #ffc107;  // Warning yellow
  border-radius: 0.5rem;
  background-color: #fff9e6;
  animation: slideIn 0.4s ease-out;
}

.change-proposal.alert-success {
  border-color: #28a745;
  background-color: #e6f9e6;
}
```

---

### Infrastructure Diagram

**Diagram Canvas:**
```scss
.architecture-diagram__canvas {
  position: relative;
  overflow-x: auto;
  padding: 1.5rem;
  border: 1px solid var(--bs-border-color-translucent);
  border-radius: var(--bs-border-radius-lg);
}
```

**Nodes:**
```scss
.architecture-diagram__node {
  border: 1px solid rgba($gray-900, 0.08);
  border-radius: var(--bs-border-radius-lg);
  background: $gray-100;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
  color: $gray-900;
}
```
- **Type label**: `letter-spacing: 0.08em; color: rgba($gray-900, 0.65);`
- **Name**: `color: $gray-800;`
- **Metadata**: `color: rgba($gray-900, 0.72); font-family: var(--bs-font-monospace); word-break: break-all;`

**Connections:**
```scss
.architecture-diagram__connection {
  fill: none;
  stroke: var(--bs-primary);
  stroke-width: 1.4;
  stroke-linecap: round;
  stroke-linejoin: round;
  opacity: 0.75;
  filter: drop-shadow(0 2px 6px rgba($primary, 0.25));
}
```

---

## 7. LAYOUT_PATTERNS

### Application Shell

```scss
body {
  background-color: $body-bg;  // #2A2A38
  color: $body-color;  // #EAEAEA
  overflow: hidden;
  font-family: $base-font;  // 'Inter', ...
}

#main-content-container {
  background-color: $main-content-bg;  // #2A2A38
  color: $main-content-text;  // #EAEAEA
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  margin: 0 !important;
  margin-right: 2.5rem !important;
}

.col-sidebar {
  width: 20%;
  display: flex;
  flex-direction: column;
  height: calc(100vh - 3.75rem);
  min-height: 0;
  overflow: hidden;
}

.col-main-content {
  padding-top: 1rem;
  padding-bottom: 3rem !important;
  width: 80%;
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: calc(100vh - 3.75rem);
  overflow-y: auto;
}
```

**Sidebar Collapsed State:**
```scss
body.sidebar-collapsed {
  .col-sidebar {
    display: none !important;
  }
  .col-main-content {
    width: 100% !important;
    margin-left: 1.5rem;
  }
}
```

**Sidebar Toggle Button:**
```scss
.sidebar-toggle-btn {
  padding: 0.25rem 0.5rem;
  font-size: 0.875rem;
  opacity: 0.7;
}

.sidebar-show-btn {
  display: none;
  position: fixed;
  top: 3.125rem;
  left: 0;
  z-index: 1000;
  // ... (visible when sidebar collapsed)
}
```

---

### Scrollable Sections

**Tab Content:**
```scss
.tab-content {
  height: calc(100vh - 5.5rem);
  overflow-y: scroll;
}

.tab-pane {
  padding-top: 1rem;
}
```

**Sidebar Scroll:**
```scss
.list-group {
  flex: 1 1 auto !important;
  min-height: 0;
  overflow-y: auto;
  background-color: $sidebar-bg;
}
```

**Message Scroll:**
```scss
.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 1.5rem;

  &::-webkit-scrollbar {
    width: 8px;
  }
  &::-webkit-scrollbar-track {
    background: #f1f1f1;
  }
  &::-webkit-scrollbar-thumb {
    background: #888;
    border-radius: 4px;
  }
}
```

---

## 8. RESPONSIVE_BEHAVIOR

### Breakpoints

Use Bootstrap's default breakpoints:
- `xs`: < 576px
- `sm`: ≥ 576px
- `md`: ≥ 768px
- `lg`: ≥ 992px
- `xl`: ≥ 1200px
- `xxl`: ≥ 1400px

### Mobile Adaptations

**Overview Cards (< 768px):**
```scss
@media (max-width: 768px) {
  .business-overview,
  .initiative-overview,
  .task-overview,
  .iteration-overview {
    padding: 1.75rem;
    border-radius: 1rem;

    &__header {
      flex-direction: column;
      align-items: flex-start;
    }

    &__section {
      padding: 1.5rem;
    }
  }
}
```

**Conversations (< 768px):**
```scss
@media (max-width: 768px) {
  .conversation-layout {
    flex-direction: column;
  }
  .conversation-sidebar {
    width: 100%;
    max-height: 200px;
    border-right: none;
    border-bottom: 1px solid #dee2e6;
  }
  .conversation-message.message-user {
    margin-left: 1rem;
  }
  .conversation-message.message-assistant {
    margin-right: 1rem;
  }
}
```

**Architecture Diagram (< 992px):**
```scss
@media (max-width: 992px) {
  .architecture-diagram__legend {
    gap: 0.75rem;
  }
  .architecture-diagram__legend-card {
    min-width: 200px;
  }
  .architecture-diagram__canvas {
    padding: 1rem;
  }
  .architecture-diagram__column {
    min-width: 220px;
  }
}
```

---

## 9. ACCESSIBILITY_REQUIREMENTS

- **WCAG AA Compliance**: Text must meet contrast ratio requirements. Test light text on dark backgrounds carefully.
- **Focus Indicators**: All interactive elements must show visible focus outline (`:focus-visible` with 2px solid primary, 2px offset).
- **ARIA Attributes**: Use `aria-disabled="true"` for disabled buttons, `aria-label` for icon-only buttons, `aria-expanded` for collapsible sections.
- **Keyboard Navigation**: Ensure tab order is logical, Enter/Space activates buttons, Escape closes modals.
- **Semantic HTML**: Use `<button>` for actions, `<a>` for navigation, `<nav>`, `<main>`, `<article>`, `<section>` appropriately.
- **Alt Text**: All images (logos, avatars, diagrams) must have descriptive `alt` attributes.

---

## 10. ANIMATIONS_AND_TRANSITIONS

### Standard Transitions
```scss
transition: all 0.15s ease;  // Hover effects, subtle changes
transition: all 0.2s ease;  // Sidebar, larger components
transition: transform 0.15s ease, box-shadow 0.15s ease;  // Card hover
```

### Keyframe Animations

**Fade In:**
```scss
@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.conversation-message {
  animation: fadeIn 0.3s ease-in;
}
```

**Slide In:**
```scss
@keyframes slideIn {
  from {
    opacity: 0;
    transform: translateX(-20px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

.change-proposal {
  animation: slideIn 0.4s ease-out;
}
```

**Spin (Loading):**
```scss
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.spinner {
  animation: spin 1s linear infinite;
}
```

---

## 11. SPECIAL_COMPONENTS

### Loading Indicator
```scss
.loading-indicator {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2rem;
  color: #6c757d;

  .spinner {
    display: inline-block;
    width: 2rem;
    height: 2rem;
    border: 3px solid rgba(0, 0, 0, 0.1);
    border-radius: 50%;
    border-top-color: #0d6efd;
    animation: spin 1s linear infinite;
    margin-right: 1rem;
  }
}
```

### Code File Tree
```scss
.code-file-tree {
  font-size: 0.95rem;

  &__list {
    list-style: none;
    margin: 0;
    padding-left: 1rem;
  }

  &__item {
    margin-bottom: 0.5rem;
  }

  &__summary {
    cursor: pointer;
  }

  &__file {
    font-weight: 600;
  }

  &__iterations {
    list-style: none;
    margin: 0.25rem 0 0 0.75rem;
    padding: 0;
  }
}
```

### Task Card
```scss
.task_card {
  .badge {
    width: 90px;  // Fixed width for consistent alignment
  }
}
```

### Markdown Container
```scss
.markdown-container {
  h1 { font-size: 1.5rem; }
  h2 { font-size: 1.25rem; }
  h3, h4, h5 { font-size: 1rem; }
}
```

---

## 12. CUSTOM_UTILITY_CLASSES

### Erie Typography
```scss
.erie-title { margin: 0; font-size: 1.125rem; font-weight: 600; color: inherit; }
.erie-title--large { font-size: 1.5rem; font-weight: 600; }
.erie-subtitle { margin: 0; font-size: 0.9rem; color: rgba($white, 0.65); }
.erie-subtitle--section { font-size: 0.85rem; color: rgba($light, 0.55); letter-spacing: 0.05em; }
.erie-label { font-size: 0.75rem; letter-spacing: 0.08em; text-transform: uppercase; color: rgba($white, 0.45); }
.erie-label--meta { font-size: 0.7rem; letter-spacing: 0.06em; color: rgba($white, 0.4); }
.erie-meta-value { font-size: 0.9rem; color: rgba($white, 0.85); word-break: break-word; }
.erie-meta-value--large { font-size: 1rem; font-weight: 600; }
```

### Hover Lift (for clickable cards)
```scss
.hover-lift {
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.hover-lift:hover {
  transform: translateY(-2px);
  box-shadow: 0 10px 20px rgba($primary, 0.25);
}
```

---

## 13. IMPLEMENTATION_NOTES

### SCSS Organization
- **`_erie_colors.scss`**: All color tokens, theme colors, business status colors.
- **`_erie_base_container.scss`**: Body, navigation, sidebar, main content layout, scrollable containers.
- **`_erie_card.scss`**: Card component overrides (minimal).
- **`_erie_task_card.scss`**: Task-specific card styling.
- **`_business_overview.scss`**: Overview component mixin applied to `.business-overview`, `.initiative-overview`, `.task-overview`, `.iteration-overview`.
- **`_infra_diagram.scss`**: Architecture diagram component.
- **`_business_conversations.scss`**: Conversation/messaging UI.
- **`_erie_json_to_div.scss`**: JSON display utilities (if needed).
- **`_erie_codefile_view.scss`**: Code file tree and display.
- **`_pubsub_messages.scss`**: PubSub message list/display.
- **`_pubsub_message_details.scss`**: PubSub message detail view.
- **`style.scss`**: Main entry point importing all partials, plus Bootstrap and utility overrides.

### Compilation
```bash
npm run compile-ui  # Build Backbone.js/Bootstrap bundle
npm run watch       # Auto-rebuild on file changes
```

### Browser Compatibility
- Modern browsers (Chrome, Firefox, Safari, Edge) with ES6+ support.
- Backdrop-filter (used in nav) requires modern browser; fallback background acceptable.

---

## 14. FUTURE_CONSIDERATIONS

- **Light Theme**: Currently dark-first. If light theme needed, create `_erie_colors_light.scss` with inverted tokens and a theme toggle.
- **Theming for Multiple Businesses**: Consider business-scoped color overrides (e.g., each business could have a custom primary color).
- **Component Library**: Extract reusable components (overview cards, status badges, diagram nodes) into a standalone library if scaling to multiple frontends.
- **Performance**: Monitor bundle size, lazy-load heavy components (diagram rendering), virtualize long lists (PubSub messages, task tables).

---

## 15. SUMMARY_CHECKLIST

When implementing or reviewing UI code, ensure:

- [ ] All spacing uses Bootstrap's 0-5 scale or custom component SCSS (no inline arbitrary values).
- [ ] Colors reference Sass tokens from `_erie_colors.scss` (no hardcoded hex outside tokens).
- [ ] Dark backgrounds for primary content, light backgrounds only for code/diagrams.
- [ ] Typography follows the defined scale and letter-spacing/text-transform rules.
- [ ] Interactive elements have hover, active, focus, and disabled states.
- [ ] Animations are ≤0.4s, use defined keyframes or transitions.
- [ ] Cards/panels use glassomorphic gradients, borders, and shadows as specified.
- [ ] Navigation (top nav, sidebar) follows gradient, shadow, and active state patterns.
- [ ] Tables use `table-sm`, `table-hover`, uppercase headers, and custom striping.
- [ ] Badges are `rounded-pill`, use semantic or business status colors.
- [ ] Layout uses Flexbox, Bootstrap grid, and responsive breakpoints.
- [ ] ARIA attributes and focus indicators are present for accessibility.
- [ ] SCSS is organized into logical partials imported by `style.scss`.
- [ ] No Bootstrap overrides via `!important` except documented edge cases.
- [ ] Mobile responsiveness tested at 768px and 992px breakpoints.

---

*This specification reflects the current implementation of Erie Iron's UI as of December 2025. For questions or proposed changes, consult the design system owner or update this document via pull request.*
