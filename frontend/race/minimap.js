// minimap.js
const TEAM_COLORS = ['#ef4444','#3b82f6','#22c55e','#f59e0b','#a855f7','#06b6d4'];

// TODO: update world bounds after airacer.wbt track geometry is finalized
const WORLD = { xMin: -80, xMax: 80, yMin: -70, yMax: 70 };

function worldToCanvas(x, y, W, H) {
  const cx = (x - WORLD.xMin) / (WORLD.xMax - WORLD.xMin) * W;
  const cy = (1 - (y - WORLD.yMin) / (WORLD.yMax - WORLD.yMin)) * H;
  return [cx, cy];
}

function drawTrackBackground(canvas) {
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  ctx.fillStyle = '#111';
  ctx.fillRect(0, 0, W, H);

  // TODO: replace with actual track outline from airacer.wbt
  // Placeholder: oval with main straight and hairpin suggestion

  // Outer curb (red/white)
  ctx.lineWidth = 44;
  ctx.strokeStyle = '#3a1a1a';
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.beginPath();
  ctx.ellipse(W / 2, H / 2, W * 0.37, H * 0.32, 0, 0, Math.PI * 2);
  ctx.stroke();

  // Track asphalt surface
  ctx.lineWidth = 38;
  ctx.strokeStyle = '#2a2a2a';
  ctx.beginPath();
  ctx.ellipse(W / 2, H / 2, W * 0.37, H * 0.32, 0, 0, Math.PI * 2);
  ctx.stroke();

  // Road surface
  ctx.lineWidth = 26;
  ctx.strokeStyle = '#555';
  ctx.beginPath();
  ctx.ellipse(W / 2, H / 2, W * 0.37, H * 0.32, 0, 0, Math.PI * 2);
  ctx.stroke();

  // Lane markings (dashed center line)
  ctx.lineWidth = 2;
  ctx.strokeStyle = 'rgba(255,255,255,0.35)';
  ctx.setLineDash([8, 8]);
  ctx.beginPath();
  ctx.ellipse(W / 2, H / 2, W * 0.37, H * 0.32, 0, 0, Math.PI * 2);
  ctx.stroke();
  ctx.setLineDash([]);

  // Start/finish line
  const [sx, sy] = [W / 2, H / 2 - H * 0.32];
  // Checkered pattern
  const sqSize = 4;
  const lineW = 36;
  const lineH = 8;
  for (let col = 0; col < Math.ceil(lineW / sqSize); col++) {
    for (let row = 0; row < Math.ceil(lineH / sqSize); row++) {
      ctx.fillStyle = (col + row) % 2 === 0 ? '#fff' : '#000';
      ctx.fillRect(
        sx - lineW / 2 + col * sqSize,
        sy - lineH / 2 + row * sqSize,
        sqSize, sqSize
      );
    }
  }

  // S/F label
  ctx.fillStyle = '#fff';
  ctx.font = 'bold 10px monospace';
  ctx.textAlign = 'center';
  ctx.fillText('S/F', sx, sy - 10);

  // Infield grass
  ctx.beginPath();
  ctx.ellipse(W / 2, H / 2, W * 0.37 - 26, H * 0.32 - 26, 0, 0, Math.PI * 2);
  ctx.fillStyle = '#1a2e1a';
  ctx.fill();

  // Pit lane indicator
  ctx.fillStyle = '#888';
  ctx.font = '9px monospace';
  ctx.textAlign = 'center';
  ctx.fillText('内场', W / 2, H / 2 + 4);
}

function drawCars(ctx, frame, W, H) {
  if (!frame || !frame.cars) return;
  frame.cars.forEach((car, i) => {
    const [cx, cy] = worldToCanvas(car.x, car.y, W, H);
    const color = car.status === 'disqualified' ? '#555' : TEAM_COLORS[i % TEAM_COLORS.length];

    // Shadow
    ctx.beginPath();
    ctx.arc(cx, cy, 9, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(0,0,0,0.4)';
    ctx.fill();

    // Glow ring for leader
    if (i === 0) {
      ctx.beginPath();
      ctx.arc(cx, cy, 11, 0, Math.PI * 2);
      ctx.strokeStyle = color + '88';
      ctx.lineWidth = 3;
      ctx.stroke();
    }

    // Car circle
    ctx.beginPath();
    ctx.arc(cx, cy, 7, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 1.5;
    ctx.stroke();

    // Direction arrow (heading: 0=+X, increases CCW)
    // Canvas Y is flipped, so negate heading
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(-car.heading + Math.PI / 2);
    ctx.beginPath();
    ctx.moveTo(0, -13);
    ctx.lineTo(4, -7);
    ctx.lineTo(-4, -7);
    ctx.closePath();
    ctx.fillStyle = '#fff';
    ctx.fill();
    ctx.restore();

    // Label with background
    const label = car.team_id;
    ctx.font = 'bold 10px monospace';
    const labelW = ctx.measureText(label).width + 4;
    ctx.fillStyle = 'rgba(0,0,0,0.6)';
    ctx.fillRect(cx + 10, cy - 8, labelW, 14);
    ctx.fillStyle = color;
    ctx.textAlign = 'left';
    ctx.fillText(label, cx + 12, cy + 4);
  });
}
