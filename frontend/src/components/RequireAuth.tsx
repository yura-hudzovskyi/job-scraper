import type { PropsWithChildren } from "react";
import { Navigate } from "react-router-dom";

import { getToken } from "../api/client";

export function RequireAuth({ children }: PropsWithChildren) {
  if (!getToken()) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}
