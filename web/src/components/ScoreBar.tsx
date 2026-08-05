import { Progress, Typography } from "antd";

const { Text } = Typography;

interface ScoreBarProps {
  label: string;
  score: number;
  color?: string;
}

export default function ScoreBar({ label, score, color = "#1677ff" }: ScoreBarProps) {
  const safeScore = Number(score ?? 0);
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
        <Text type="secondary">{label}</Text>
        <Text strong>{(safeScore * 100).toFixed(0)}%</Text>
      </div>
      <Progress percent={Math.round(safeScore * 100)} strokeColor={color} showInfo={false} size="small" />
    </div>
  );
}
