"use client";
import { useEffect, useRef } from "react";
import { agents, AgentStates } from "./types";

export default function Office({ states }: { states: AgentStates }) {
  const container = useRef<HTMLDivElement>(null);
  const stateRef = useRef(states);
  stateRef.current = states;
  useEffect(() => {
    let disposed = false;
    let game: import("phaser").Game | undefined;
    void import("phaser").then(({ default: Phaser }) => {
      if (disposed || !container.current) return;
      class OfficeScene extends Phaser.Scene {
        dots: import("phaser").GameObjects.Arc[] = [];
        labels: import("phaser").GameObjects.Text[] = [];
        create() {
          const g = this.add.graphics();
          g.fillStyle(0x182528); g.fillRoundedRect(12, 12, 736, 296, 20);
          g.lineStyle(1, 0x2b3d3e);
          for (let x = 32; x < 740; x += 32) g.lineBetween(x, 22, x, 298);
          for (let y = 22; y < 298; y += 32) g.lineBetween(22, y, 738, y);
          g.lineStyle(3, 0x49655b); g.lineBetween(180, 145, 580, 145);
          this.add.text(380, 272, "RESEARCH  →  COMPARE  →  BUILD  →  RECOMMEND", { fontSize: "11px", color: "#9cb4ac" }).setOrigin(0.5);
          agents.forEach((agent, i) => {
            const x = 95 + i * 190;
            g.fillStyle(0x334641); g.fillRoundedRect(x - 61, 176, 122, 42, 8);
            g.fillStyle(0x152121); g.fillRoundedRect(x - 24, 179, 48, 24, 4);
            g.fillStyle(0x6ba58d); g.fillCircle(x, 129, 20);
            g.fillStyle(0xbddbc9); g.fillCircle(x, 104, 13);
            this.add.text(x, 54, agent, { fontSize: "13px", color: "#e6f2ec" }).setOrigin(0.5);
            this.dots.push(this.add.circle(x + 38, 104, 6, 0x708078));
            this.labels.push(this.add.text(x, 238, "idle", { fontSize: "12px", color: "#9cb4ac" }).setOrigin(0.5));
          });
        }
        update(time: number) {
          const colors = { idle: 0x708078, working: 0xf5c76b, completed: 0x76d4a8, error: 0xf18484 };
          agents.forEach((agent, i) => {
            const status = stateRef.current[agent];
            this.dots[i]?.setFillStyle(colors[status]).setAlpha(status === "working" ? 0.55 + Math.sin(time / 180) * 0.45 : 1);
            this.labels[i]?.setText(status);
          });
        }
      }
      game = new Phaser.Game({ type: Phaser.AUTO, width: 760, height: 320, backgroundColor: "#101c1f", parent: container.current,
        scene: OfficeScene, scale: { mode: Phaser.Scale.FIT, autoCenter: Phaser.Scale.CENTER_BOTH }, audio: { noAudio: true } });
    });
    return () => { disposed = true; game?.destroy(true); };
  }, []);
  return <div className="office-canvas" ref={container} aria-label="Animated virtual office. Agent statuses are listed below." />;
}
