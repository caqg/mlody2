Example usage with YOLOv26 model checkpoints:

```bash
mlody/teams/framera/yolo26/framera_yolo26_nvidia \
  --device 1 \
  --width 640 \
  --height 480 \
  --fps 30 \
  --gpu \
  --gui
```

Run segmentation with the task-specific default `-seg` model:

```bash
mlody/teams/framera/yolo26/framera_yolo26_nvidia \
  --task segmentation \
  --gpu \
  --gui
```

In `--task segmentation --gui`, colored transparent segment layers are composited over the live video, with boxes/labels still shown.
The HUD shows `Segments: N`; if `N` stays `0`, verify you are using a `*-seg.pt` model.

To isolate one segmentation class and paint everything else green:

```bash
mlody/teams/framera/yolo26/framera_yolo26_nvidia \
  --task segmentation \
  --isolate 0 \
  --gpu \
  --gui
```

For headless JSONL output, omit `--gui` and keep JSON enabled (default).

Default camera device is `1` (override with `--device` as needed).
Default model uses suffix mapping based on `--task`: detection -> `""`, segmentation -> `"-seg"` (for example `yolo26x.pt` vs `yolo26x-seg.pt`).
