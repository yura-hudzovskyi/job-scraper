import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { ApiError, setToken } from "../api/client";
import { login } from "../api/endpoints";
import { Button, Card, ErrorBanner, Field, SectionTitle, inputClass } from "../components/ui";

export function Login() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const loginMutation = useMutation({
    mutationFn: () => login(email, password),
    onSuccess: (data) => {
      setToken(data.access_token);
      navigate("/");
    },
  });

  return (
    <div className="mx-auto mt-16 max-w-sm">
      <Card>
        <SectionTitle>Log in</SectionTitle>
        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            loginMutation.mutate();
          }}
        >
          <Field label="Email">
            <input
              type="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className={inputClass}
            />
          </Field>
          <Field label="Password">
            <input
              type="password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className={inputClass}
            />
          </Field>
          <Button type="submit" disabled={loginMutation.isPending}>
            {loginMutation.isPending ? "Logging in…" : "Log in"}
          </Button>
          {loginMutation.isError && (
            <ErrorBanner
              message={
                loginMutation.error instanceof ApiError
                  ? loginMutation.error.message
                  : "Login failed"
              }
            />
          )}
        </form>
        <p className="mt-4 text-sm text-slate-600">
          No account yet?{" "}
          <Link to="/register" className="font-medium text-slate-900 underline">
            Register
          </Link>
        </p>
      </Card>
    </div>
  );
}
