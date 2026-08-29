import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { ApiError, setToken } from "../api/client";
import { register } from "../api/endpoints";
import { Button, Card, ErrorBanner, Field, SectionTitle, inputClass } from "../components/ui";

export function Register() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const registerMutation = useMutation({
    mutationFn: () => register(email, password),
    onSuccess: (data) => {
      setToken(data.access_token);
      navigate("/");
    },
  });

  return (
    <div className="mx-auto mt-16 max-w-sm">
      <Card>
        <SectionTitle>Create an account</SectionTitle>
        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            registerMutation.mutate();
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
              minLength={8}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className={inputClass}
            />
          </Field>
          <Button type="submit" disabled={registerMutation.isPending}>
            {registerMutation.isPending ? "Creating account…" : "Register"}
          </Button>
          {registerMutation.isError && (
            <ErrorBanner
              message={
                registerMutation.error instanceof ApiError
                  ? registerMutation.error.message
                  : "Registration failed"
              }
            />
          )}
        </form>
        <p className="mt-4 text-sm text-slate-600">
          Already have an account?{" "}
          <Link to="/login" className="font-medium text-slate-900 underline">
            Log in
          </Link>
        </p>
      </Card>
    </div>
  );
}
