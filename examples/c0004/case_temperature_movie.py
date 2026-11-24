from case_mesh import g, np, out, paths
from PIL import Image, ImageDraw, ImageFont
import json
import sys
import time

start = time.perf_counter()
dst = out / sys.argv[1]
timing = json.loads((dst / 'thermal_timing.json').read_text(encoding='utf8'))
mesh = np.load(dst / 'mesh.npz')
history = np.load(dst / 'T.npy', mmap_mode='r')
times, ids = mesh['times'], mesh['ids']
masks = [mesh['cells'][:,0] == side for side in (1,0)]
stops = [25, 100, 250, 500, 1000, 2000, 3800]
colors = np.array([[210,228,247],[104,169,206],[53,121,130],[85,158,93],
                   [250,196,67],[223,88,42],[130,14,75]])
lut = np.stack([np.interp(np.arange(3801), stops, c) for c in colors.T], axis=1).astype('uint8')
fonts = {s:ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',s) for s in (15,18,22,26)}
w, h = 244, round(244*ids.shape[1]/ids.shape[2])
frames = []
for target in np.linspace(0,4,201):
    idx = np.argmin(abs(times-target))
    t = times[idx] if target else 0.
    temp = history[idx].max(axis=0)-273.15 if target else np.full(history.shape[-1],timing['initial_C'])
    im = Image.new('RGB',(740,h+228),'white')
    draw = ImageDraw.Draw(im)
    def label(x,y,s,sz):
        draw.text((x,y),s,font=fonts[sz],fill='#193849')
    active = next(((j,p,arc,pts) for j,(p,arc,pts) in enumerate(paths,1)
                   if p['start_s']<=t<p['end_s']),None)
    phase = f'第 {active[0]} 段焊接' if active else ('焊后冷却' if t>=paths[-1][0]['end_s'] else '段间停光')
    label(24,14,f'C0004 双板温度   /   初温 {timing["initial_C"]:g} °C',26)
    label(24,61,f'{t:.4f} s    {phase}',22)
    label(408,65,'各位置取厚度五层中的最高温',18)
    for col,side in enumerate((1,0)):
        left,top = 61+col*367,136
        pixels = ids[side,::-1]
        rgb = lut[np.clip(temp.astype(int),0,3800)][pixels]
        rgb[pixels<0] = 255
        im.paste(Image.fromarray(rgb).resize((w,h),Image.Resampling.NEAREST),(left,top))
        label(left,101,('上板' if side else '底板')+f'   本帧最高 {temp[masks[col]].max():.0f} °C',18)
        if active:
            _,p,arc,pts = active
            pos = (t-p['start_s'])*p['speed_mm_s']
            xx = left+(np.interp(pos,arc,pts[:,0])-g['x'][0]+.05)/(.1*pixels.shape[1])*w
            yy = top+(g['y'][-1]-np.interp(pos,arc,pts[:,1])+.05)/(.1*pixels.shape[0])*h
            draw.ellipse((xx-6,yy-6,xx+6,yy+6),outline='#202c38',width=2)
    y = 148+h
    label(24,y,'温度 °C',15)
    for j,(v,c) in enumerate(zip(stops,colors)):
        x = 110+j*85
        draw.rectangle((x,y+4,x+12,y+16),fill=tuple(c))
        label(x+16,y,str(v),15)
    label(24,y+36,'圆圈标出激光位置。两板及全部时刻共用色标。',15)
    frames.append(im.quantize(colors=128))
dest = dst / '双板焊接温度历程.gif'
frames[0].save(dest,save_all=True,append_images=frames[1:],duration=[80]*200+[800],loop=0,optimize=False)
with Image.open(dest) as movie:
    assert movie.n_frames == len(frames)
    movie.seek(movie.n_frames-1)
    movie.load()
heat = np.load(dst/'heat.npz')
timing.update(movie_s=time.perf_counter()-start, peak_C=[float(heat['peak_K'][:,m].max()-273.15) for m in masks],
              end_C=[float(heat['end_K'].min()-273.15),float(heat['end_K'].max()-273.15)])
(dst/'运行记录.json').write_text(json.dumps(timing,ensure_ascii=False,indent=2),encoding='utf8')
print(timing)
