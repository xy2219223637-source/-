type IcpFilingProps = {
  className?: string;
};

export default function IcpFiling({ className = '' }: IcpFilingProps) {
  return (
    <a
      className={`text-xs text-[var(--muted)] transition-colors hover:text-[var(--text)] ${className}`}
      href="https://beian.miit.gov.cn/"
      target="_blank"
      rel="noopener noreferrer"
    >
      浙ICP备2026069719号-1
    </a>
  );
}
