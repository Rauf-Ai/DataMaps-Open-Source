import { useAppTheme } from "./main.jsx";

export function Logo({ className }) {
  const { theme } = useAppTheme();
  return <img src={theme === "dark" ? "/logo-dark.svg" : "/logo-light.svg"} alt="DataMaps" className={className} />;
}
