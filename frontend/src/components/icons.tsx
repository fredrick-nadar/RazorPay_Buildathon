/**
 * Rich Animated Lucide Icon Set for ARGUS CONTROL.
 * Modern, crisp, animated Lucide icons with fluid micro-interactions.
 */

import type { LucideProps } from "lucide-react";
import {
  Home,
  Key,
  Plug,
  Brain,
  Activity,
  CreditCard,
  Tag,
  MessageSquare,
  Volume2,
  Mic,
  Languages,
  BookOpen,
  PanelLeft,
  ChevronUp,
  ChevronDown,
  Layers,
  CornerUpLeft,
  Clock,
  HelpCircle,
  Check,
  CheckCheck,
  X,
  Flag,
  Search,
  Zap,
  Shield,
  Copy,
  Scale,
  ScrollText,
  GitFork,
  RotateCcw,
  Presentation,
} from "lucide-react";

export type IconProps = LucideProps;

export function IconHome({ size = 16, className = "", ...props }: IconProps) {
  return <Home size={size} className={`transition-transform duration-200 group-hover:scale-110 ${className}`} {...props} />;
}

export function IconKey({ size = 16, className = "", ...props }: IconProps) {
  return <Key size={size} className={`transition-transform duration-200 group-hover:rotate-12 ${className}`} {...props} />;
}

export function IconPlug({ size = 16, className = "", ...props }: IconProps) {
  return <Plug size={size} className={`transition-transform duration-200 group-hover:scale-110 ${className}`} {...props} />;
}

export function IconBrain({ size = 16, className = "", ...props }: IconProps) {
  return <Brain size={size} className={`transition-transform duration-300 group-hover:scale-115 group-hover:rotate-6 ${className}`} {...props} />;
}

export function IconUsage({ size = 16, className = "", ...props }: IconProps) {
  return <Activity size={size} className={`transition-transform duration-200 group-hover:scale-110 ${className}`} {...props} />;
}

export function IconBilling({ size = 16, className = "", ...props }: IconProps) {
  return <CreditCard size={size} className={`transition-transform duration-200 group-hover:scale-110 ${className}`} {...props} />;
}

export function IconPricing({ size = 16, className = "", ...props }: IconProps) {
  return <Tag size={size} className={`transition-transform duration-200 group-hover:-rotate-12 ${className}`} {...props} />;
}

export function IconChat({ size = 16, className = "", ...props }: IconProps) {
  return <MessageSquare size={size} className={`transition-transform duration-200 group-hover:scale-110 ${className}`} {...props} />;
}

export function IconSpeaker({ size = 16, className = "", ...props }: IconProps) {
  return <Volume2 size={size} className={`transition-transform duration-200 group-hover:scale-115 ${className}`} {...props} />;
}

export function IconMic({ size = 16, className = "", ...props }: IconProps) {
  return <Mic size={size} className={`transition-transform duration-200 group-hover:scale-115 ${className}`} {...props} />;
}

export function IconLanguages({ size = 16, className = "", ...props }: IconProps) {
  return <Languages size={size} className={`transition-transform duration-200 group-hover:scale-110 ${className}`} {...props} />;
}

export function IconActivity({ size = 16, className = "", ...props }: IconProps) {
  return <Activity size={size} className={`transition-transform duration-300 group-hover:scale-115 ${className}`} {...props} />;
}

export function IconBookOpen({ size = 16, className = "", ...props }: IconProps) {
  return <BookOpen size={size} className={`transition-transform duration-200 group-hover:scale-110 ${className}`} {...props} />;
}

export function IconSidebar({ size = 16, className = "", ...props }: IconProps) {
  return <PanelLeft size={size} className={`transition-transform duration-200 group-hover:scale-110 ${className}`} {...props} />;
}

export function IconChevronUp({ size = 16, className = "", ...props }: IconProps) {
  return <ChevronUp size={size} className={`transition-transform duration-200 group-hover:-translate-y-0.5 ${className}`} {...props} />;
}

export function IconChevronDown({ size = 16, className = "", ...props }: IconProps) {
  return <ChevronDown size={size} className={`transition-transform duration-200 group-hover:translate-y-0.5 ${className}`} {...props} />;
}

export function IconLayers({ size = 16, className = "", ...props }: IconProps) {
  return <Layers size={size} className={`transition-transform duration-200 group-hover:scale-110 group-hover:-translate-y-0.5 ${className}`} {...props} />;
}

export function IconCornerUpLeft({ size = 16, className = "", ...props }: IconProps) {
  return <CornerUpLeft size={size} className={`transition-transform duration-200 group-hover:-translate-x-0.5 ${className}`} {...props} />;
}

export function IconClock({ size = 16, className = "", ...props }: IconProps) {
  return <Clock size={size} className={`transition-transform duration-200 group-hover:rotate-45 ${className}`} {...props} />;
}

export function IconQuestion({ size = 16, className = "", ...props }: IconProps) {
  return <HelpCircle size={size} className={`transition-transform duration-200 group-hover:scale-110 group-hover:rotate-12 ${className}`} {...props} />;
}

export function IconCheck({ size = 16, className = "", ...props }: IconProps) {
  return <Check size={size} className={`transition-transform duration-200 group-hover:scale-115 ${className}`} {...props} />;
}

export function IconDoubleCheck({ size = 16, className = "", ...props }: IconProps) {
  return <CheckCheck size={size} className={`transition-transform duration-200 group-hover:scale-115 ${className}`} {...props} />;
}

export function IconX({ size = 16, className = "", ...props }: IconProps) {
  return <X size={size} className={`transition-transform duration-200 group-hover:scale-110 group-hover:rotate-90 ${className}`} {...props} />;
}

export function IconFlag({ size = 16, className = "", ...props }: IconProps) {
  return <Flag size={size} className={`transition-transform duration-200 group-hover:scale-115 group-hover:rotate-12 ${className}`} {...props} />;
}

export function IconSearch({ size = 16, className = "", ...props }: IconProps) {
  return <Search size={size} className={`transition-transform duration-200 group-hover:scale-110 ${className}`} {...props} />;
}

export function IconBolt({ size = 16, className = "", ...props }: IconProps) {
  return <Zap size={size} className={`transition-transform duration-300 group-hover:scale-120 group-hover:rotate-12 ${className}`} {...props} />;
}

export function IconShield({ size = 16, className = "", ...props }: IconProps) {
  return <Shield size={size} className={`transition-transform duration-200 group-hover:scale-110 group-hover:-translate-y-0.5 ${className}`} {...props} />;
}

export function IconCopy({ size = 16, className = "", ...props }: IconProps) {
  return <Copy size={size} className={`transition-transform duration-200 group-hover:scale-110 ${className}`} {...props} />;
}

export function IconScale({ size = 16, className = "", ...props }: IconProps) {
  return <Scale size={size} className={`transition-transform duration-200 group-hover:scale-110 group-hover:rotate-6 ${className}`} {...props} />;
}

export function IconScroll({ size = 16, className = "", ...props }: IconProps) {
  return <ScrollText size={size} className={`transition-transform duration-200 group-hover:scale-110 group-hover:-translate-y-0.5 ${className}`} {...props} />;
}

export function IconRoute({ size = 16, className = "", ...props }: IconProps) {
  return <GitFork size={size} className={`transition-transform duration-200 group-hover:scale-110 group-hover:rotate-12 ${className}`} {...props} />;
}

export function IconRefresh({ size = 16, className = "", ...props }: IconProps) {
  return <RotateCcw size={size} className={`transition-transform duration-500 group-hover:-rotate-180 ${className}`} {...props} />;
}

export function IconPresentation({ size = 16, className = "", ...props }: IconProps) {
  return <Presentation size={size} className={`transition-transform duration-200 group-hover:scale-110 ${className}`} {...props} />;
}
