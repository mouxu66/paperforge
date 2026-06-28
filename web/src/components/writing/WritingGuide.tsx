import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Card, Drawer, Space, Tag, Typography } from 'antd'
import { BookOutlined, CheckOutlined } from '@ant-design/icons'

/**
 * 写作指南 Drawer —— 按步骤教新用户如何写一篇学术论文。
 *
 * 状态管理：
 * - localStorage key `writing-guide-dismissed` 记录用户是否已关闭过面板。
 * - 首次打开编辑器（未 dismiss）时自动展开 Drawer。
 * - 用户点击「我已了解」或关闭 Drawer 后，后续不再自动展开。
 * - 仍可通过外部按钮手动打开。
 */

const DISMISS_KEY = 'writing-guide-dismissed'

interface GuideStep {
  /** 步骤编号 */
  no: number
  /** 步骤标题 */
  title: string
  /** 简短说明（该步骤是做什么的） */
  desc: string
  /** 操作提示（对应界面哪个功能） */
  tip: string
  /** 步骤标签颜色 */
  color: string
}

interface WritingGuideProps {
  /** 受控打开状态（由父组件管理，用于入口按钮触发） */
  open: boolean
  /** 关闭回调 */
  onClose: () => void
}

export default function WritingGuide({ open, onClose }: WritingGuideProps) {
  const { t } = useTranslation()
  const [dismissed, setDismissed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(DISMISS_KEY) === '1'
    } catch {
      return false
    }
  })

  const STEPS: GuideStep[] = [
    {
      no: 1,
      title: t('editor.guideStep1Title'),
      desc: t('editor.guideStep1Desc'),
      tip: t('editor.guideStep1Tip'),
      color: 'blue',
    },
    {
      no: 2,
      title: t('editor.guideStep2Title'),
      desc: t('editor.guideStep2Desc'),
      tip: t('editor.guideStep2Tip'),
      color: 'green',
    },
    {
      no: 3,
      title: t('editor.guideStep3Title'),
      desc: t('editor.guideStep3Desc'),
      tip: t('editor.guideStep3Tip'),
      color: 'orange',
    },
    {
      no: 4,
      title: t('editor.guideStep4Title'),
      desc: t('editor.guideStep4Desc'),
      tip: t('editor.guideStep4Tip'),
      color: 'purple',
    },
    {
      no: 5,
      title: t('editor.guideStep5Title'),
      desc: t('editor.guideStep5Desc'),
      tip: t('editor.guideStep5Tip'),
      color: 'cyan',
    },
  ]

  // 首次打开编辑器（未 dismiss）时自动展开 Drawer
  const [autoOpen, setAutoOpen] = useState<boolean>(false)
  useEffect(() => {
    if (!dismissed) {
      setAutoOpen(true)
    }
  }, [dismissed])

  /** 标记用户已了解，关闭面板并持久化 */
  const handleDismiss = () => {
    try {
      localStorage.setItem(DISMISS_KEY, '1')
    } catch {
      // localStorage 不可用时静默忽略
    }
    setDismissed(true)
    setAutoOpen(false)
    onClose()
  }

  // 实际打开状态：外部按钮触发 OR 首次自动展开
  const actualOpen = open || autoOpen

  const handleClose = () => {
    setAutoOpen(false)
    onClose()
  }

  return (
    <Drawer
      title={
        <Space>
          <BookOutlined style={{ color: '#1e40af' }} />
          <span className="pf-serif" style={{ fontSize: 16, fontWeight: 600 }}>
            {t('editor.guideTitle')}
          </span>
        </Space>
      }
      placement="right"
      width={420}
      open={actualOpen}
      onClose={handleClose}
      styles={{
        body: {
          padding: 16,
          background: '#f8fafc',
        },
      }}
      footer={
        <div style={{ textAlign: 'center' }}>
          <Button
            type="primary"
            icon={<CheckOutlined />}
            onClick={handleDismiss}
            style={{ borderRadius: 6 }}
          >
            {t('editor.guideDismiss')}
          </Button>
        </div>
      }
    >
      <Typography.Paragraph
        style={{ color: '#64748b', fontSize: 13, marginBottom: 16 }}
      >
        {t('editor.guideIntro')}
      </Typography.Paragraph>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {STEPS.map((step) => (
          <Card
            key={step.no}
            size="small"
            style={{
              borderRadius: 8,
              border: '1px solid #e8ecf1',
              background: '#fff',
            }}
            styles={{ body: { padding: 14 } }}
          >
            <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
              <div
                style={{
                  flexShrink: 0,
                  width: 32,
                  height: 32,
                  borderRadius: '50%',
                  background: '#1e40af',
                  color: '#fff',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 15,
                  fontWeight: 700,
                }}
              >
                {step.no}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ marginBottom: 6, display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Tag color={step.color} style={{ margin: 0 }}>
                    {t('editor.guideStepPrefix', { no: step.no })}
                  </Tag>
                  <Typography.Text
                    strong
                    className="pf-serif"
                    style={{ fontSize: 15, color: '#1a1a2e' }}
                  >
                    {step.title}
                  </Typography.Text>
                </div>
                <Typography.Paragraph
                  style={{ margin: 0, fontSize: 13, color: '#475569', lineHeight: 1.6 }}
                >
                  {step.desc}
                </Typography.Paragraph>
                <div
                  style={{
                    marginTop: 8,
                    padding: '6px 10px',
                    background: '#f1f5f9',
                    borderRadius: 6,
                    fontSize: 12,
                    color: '#1e40af',
                    borderLeft: '3px solid #1e40af',
                  }}
                >
                  💡 {step.tip}
                </div>
              </div>
            </div>
          </Card>
        ))}
      </div>
    </Drawer>
  )
}
