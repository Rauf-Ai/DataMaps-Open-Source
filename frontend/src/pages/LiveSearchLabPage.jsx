import { Navigate } from "react-router-dom";
import { useAuth } from "../AuthContext.jsx";
import { AiSearchWorkspace } from "./ai-search/AiSearchWorkspace.jsx";


export function LiveSearchLabPage() {
  const { me, loading } = useAuth();
  if (loading) return null;
  if (!me) return <Navigate to="/login?next=/" replace />;
  return <AiSearchWorkspace />;
}
