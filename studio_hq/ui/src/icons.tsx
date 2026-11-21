/**
 * Studio HQ icons: Phosphor Icons (MIT, https://phosphoricons.com).
 * Names match the ones the components already use, so the icon set can change in one place.
 * Weight defaults to "bold" via IconContext in main.tsx; navigation uses duotone and fill.
 */
export {
  Pulse as Activity,
  Archive,
  ArrowDown,
  DownloadSimple as ArrowDownToLine,
  ArrowUp,
  ArrowUpRight,
  ChartBar as BarChart3,
  BookOpenText as BookOpen,
  CalendarDots as CalendarDays,
  Check,
  WarningCircle as CircleAlert,
  FilmSlate as Clapperboard,
  Cloud,
  Database,
  Eye,
  FilmSlate as Film,
  Globe as Globe2,
  HardDrive,
  House as Home,
  Pause,
  Play,
  ArrowsClockwise as RefreshCw,
  ArrowClockwise as RotateCw,
  Scissors,
  HardDrives as Server,
  ShieldCheck,
  MagicWand as WandSparkles,
  IconContext,
} from '@phosphor-icons/react';

/** The Studio HQ mark: a director's slate whose stripes rise like a chart. */
export function StudioLogo({ size = 40 }: { size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 64 64" role="img" aria-label="Studio HQ" className="studio-logo">
    <rect width="64" height="64" rx="16" fill="#ffcf35" />
    <rect x="12" y="25" width="40" height="27" rx="5" fill="#15130a" />
    <path d="M12 22.5 L50.5 13.2 a3 3 0 0 1 3.6 2.2 l1 4.1 L14.6 29.3 Z" fill="#15130a" />
    <path d="M23 18.3 l5.6 7.4 M34 15.6 l5.6 7.4" stroke="#ffcf35" strokeWidth="3.2" strokeLinecap="round" />
    <rect x="19" y="41" width="5.5" height="6" rx="1.5" fill="#ffcf35" />
    <rect x="29.25" y="36" width="5.5" height="11" rx="1.5" fill="#ffcf35" />
    <rect x="39.5" y="31" width="5.5" height="16" rx="1.5" fill="#ffcf35" />
  </svg>;
}
