import { useEffect, useState, useRef } from "react";

import { type Row } from "../api";
import { Modal } from "../components/ui";

export default function Teleprompter({
  draft,
  onClose,
}: {
  draft: Row;
  onClose: () => void;
}) {
  const [size, setSize] = useState(36),
    [speed, setSpeed] = useState(30),
    [play, setPlay] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!play) return;
    const timer = setInterval(() => {
      if (box.current) box.current.scrollTop += speed / 20;
    }, 50);
    return () => clearInterval(timer);
  }, [play, speed]);
  return (
    <Modal title={`提词器 · ${draft.title}`} onClose={onClose}>
      <div className="button-row">
        <label>
          字号{" "}
          <input
            type="range"
            min="24"
            max="64"
            value={size}
            onChange={(e) => setSize(+e.target.value)}
          />
        </label>
        <label>
          速度{" "}
          <input
            type="range"
            min="5"
            max="100"
            value={speed}
            onChange={(e) => setSpeed(+e.target.value)}
          />
        </label>
        <button onClick={() => setPlay(!play)}>
          {play ? "暂停" : "开始滚动"}
        </button>
        <button
          onClick={() => {
            setPlay(false);
            if (box.current) box.current.scrollTop = 0;
          }}
        >
          重置
        </button>
      </div>
      <div className="teleprompter" ref={box} style={{ fontSize: size }}>
        {draft.body}
      </div>
    </Modal>
  );
}
