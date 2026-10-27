[FCAutoDimension_Help.html](https://github.com/user-attachments/files/34009174/FCAutoDimension_Help.html)
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FC AutoDimension – Help</title>
<style>
  :root { --ink:#16202e; --muted:#5b6574; --line:#d9dee6; --accent:#1a6fd6; --bg:#ffffff; }
  body { font-family: "Segoe UI", Arial, sans-serif; color: var(--ink); background: var(--bg); max-width: 880px; margin: 0 auto; padding: 24px 16px 48px; line-height: 1.5; }
  h1 { font-size: 28px; margin: 0; color: var(--accent); } h2 { font-size: 18px; margin-top: 28px; border-bottom: 1px solid var(--line); padding-bottom: 4px; }
  .sub { color: var(--muted); margin: 2px 0 20px; letter-spacing: .5px; }
  table { border-collapse: collapse; width: 100%; font-size: 14px; } td, th { border: 1px solid var(--line); padding: 6px 8px; text-align: left; vertical-align: top; }
  th { background: #f3f5f8; } code { background: #f3f5f8; padding: 1px 4px; border-radius: 3px; }
  .note { background: #fff7e6; border: 1px solid #f0d9a8; padding: 10px 12px; border-radius: 6px; }
  footer { margin-top: 32px; color: var(--muted); font-size: 13px; }
</style>
</head>
<body>
<h1>FC AutoDimension</h1>
<p class="sub">MEP DIMENSIONING ADD-IN FOR REVIT 2023–2027 · version 1.2.1 · BIM FC</p>

<h2>Quick start</h2>
<ol>
  <li>Open a plan view (floor plan, ceiling plan or structural plan).</li>
  <li>Click <b>FC AutoDimension</b> – ribbon tab <b>FC Tools › Auto Dimension</b> (Autodesk Marketplace version: <b>Add-Ins</b> tab).</li>
  <li>Choose the <b>Mode</b>, the <b>Style</b> (colour, font, arrow) and click <b>Dimension Current View</b>.</li>
  <li>Read the Results Report. Everything created in one run is one Undo step.</li>
</ol>

<h2>Modes</h2>
<table>
  <tr><th>Mode</th><th>What it does</th><th>Typical use</th></tr>
  <tr><td>Each element to its nearest wall</td><td>One X and one Y dimension per element, from the element to the nearest parallel wall or column of its room. Parallel pipes / sprinkler rows that share a wall become one chain.</td><td>Ducts, equipment, risers, shafts, coordination drawings</td></tr>
  <tr><td>Room strings</td><td>For every room: as few chain dimensions as needed, each from the room edge on one side to the room edge on the other, through every sprinkler, branch pipe, diffuser, light or device within reach. Parallel strings start on the same wall.</td><td>Fire protection, ceiling layouts (HVAC terminals, lights, detectors), electrical</td></tr>
</table>

<h2>What is dimensioned from where</h2>
<table>
  <tr><th>Element</th><th>Dimensioned from</th></tr>
  <tr><td>Pipes, conduits, cable trays, sprinklers, air terminals, light fittings, fire-alarm detectors</td><td>Centreline / centre (default)</td></tr>
  <tr><td>Ducts, mechanical / electrical equipment, electrical fixtures and wall devices, accessories</td><td>Outside edge (default)</td></tr>
  <tr><td>Your choice</td><td>Advanced options › Categories › <b>From centre</b>: tick or untick for each category. When an element has no reference of the chosen kind, the other one is used and the Results Report says so.</td></tr>
  <tr><td>Reference</td><td>Walls and columns of the element's own room – host model or linked models. Interior-design links and finish walls are not used (except as room edges in room strings).</td></tr>
  <tr><td>Axes</td><td>Detected from the walls: rotated buildings and every wing get their own X/Y. The view and the model are never changed.</td></tr>
</table>
<ul>
  <li>Ducts and pipes are dimensioned across their run only – never from their end, cap or fitting. Runs shorter than 300 mm are ignored.</li>
  <li>Dimensions shorter than 200 mm are not created; every dimension is checked after creation.</li>
  <li>Elements already dimensioned are skipped, so you can run it again after design changes.</li>
  <li><b>Walls first</b>: a wall face is used unless another element is at least 600 mm nearer; only walls drawn in the view are used (nothing above the cut plane, such as bulkheads, or below the view depth); room strings end on walls only.</li>
  <li>No dimension longer than <b>5000 mm</b> (setting).</li>
  <li>"Centre" is the real middle of the element, even when a family's Center planes sit at a corner.</li>
</ul>

<h2>Dimension styles</h2>
<p>Choose <b>Built-in FC style – 15° filled arrow, 2.5 mm text</b> (default, loaded into any project) or <b>Pick a dimension type of this project</b> (✔ = 15° filled arrow; <b>Load 15° arrow type…</b> adds one).</p>
<p>Style <b>FC style</b> creates the add-in's own dimension type in the open project – text always <b>2.5 mm</b>, with your choice of:</p>
<ul>
  <li><b>Colour</b> – 10 quick colours or any colour (Custom…)</li>
  <li><b>Font</b> – Arial Narrow, Arial, Calibri, Tahoma, Century Gothic, ISOCPEUR</li>
  <li><b>Arrow / tick</b> – arrow 15°, 20° or 30° filled, arrow 30° open, ISO arrow 90° open, diagonal tick, filled dot, open dot</li>
</ul>
<p>Example name: <code>FC 2.5mm Arial - Blue - Diagonal tick</code>. Your own project types are never changed. Choose <b>Pick a dimension type of this project</b> to use a project type instead.</p>
<p><b>Match my dimensions in this view…</b> reads the dimensions you already drew and copies your spacing, offsets, mode and categories.</p>

<h2>Advanced options</h2>
<p>Everything else is under <b>Advanced options</b> (tabs: Axes and spacing · Categories · References · Placement · Room strings), e.g. categories to dimension, pipe sizes for room strings (default DN15–DN50), how far a string reaches (1700 mm), clearance from elements, rooms to process.</p>

<h2>Activation</h2>
<p>Click <b>Licence…</b> → <b>Email activation request</b> (or copy your Machine ID and email it to <a href="mailto:BIM.FC27@gmail.com">BIM.FC27@gmail.com</a>). Paste the code you receive and click <b>Activate</b>. A code is valid for one computer. The Autodesk Marketplace version works fully for 14 days before activation.</p>

<h2>Troubleshooting</h2>
<table>
  <tr><th>Symptom</th><th>What to do</th></tr>
  <tr><td>No FC Tools tab</td><td>Check that the installer ticked your Revit version; restart Revit; choose <i>Always Load</i> if Revit asks about the add-in.</td></tr>
  <tr><td>Button greyed out</td><td>It works only in plan views of a project.</td></tr>
  <tr><td>An element was not dimensioned</td><td>The Results Report gives the reason per element and direction; <i>Show only skipped / failed</i>, <i>Select in model</i>, <i>Export CSV</i>, <i>Open log</i>.</td></tr>
  <tr><td>Undo</td><td>One Undo step: <b>FC AutoDimension</b>.</td></tr>
</table>

<footer>FC AutoDimension © BIM FC · Support: <a href="mailto:BIM.FC27@gmail.com">BIM.FC27@gmail.com</a> · Always review automatically created dimensions before issuing drawings.</footer>
</body>
</html>
