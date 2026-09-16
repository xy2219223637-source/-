import type { ShootingTask } from '../types';

export const notificationLabels: Record<ShootingTask['notification_status'], string> = {
  not_sent: '未发送（历史记录）', sending: '发送中', sent: '已提交', failed: '发送失败', unknown: '发送结果待确认',
};

export function notificationSummary(task: ShootingTask): string {
  const recipient = task.recipient_name || task.assignee_name;
  if (task.notification_status === 'sent') return `已提交给飞书，接收对象：${recipient}。请确认收件人是否收到。`;
  if (task.notification_status === 'failed') return `拍摄任务已创建，飞书通知失败：${task.notification_error}`;
  if (task.notification_status === 'sending') return '拍摄任务已创建，飞书通知正在发送，请查看拍摄任务中的结果';
  return '拍摄任务已创建，发送结果待确认，请核对飞书消息';
}

export function isNotificationOutcomeUnknown(error: unknown): boolean {
  const response = (error as { response?: { status?: number; headers?: Record<string, unknown> } } | null)?.response;
  if (!response) return true;
  if (response.headers?.['x-notification-outcome'] === 'not_started') return false;
  return typeof response.status !== 'number' || response.status >= 500 || response.status === 408;
}
