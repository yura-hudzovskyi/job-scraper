import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ApiError } from "../api/client";
import {
  connectTelegram,
  getPreferences,
  getTelegramStatus,
  testTelegram,
  updatePreferences,
} from "../api/endpoints";
import { EMPTY_PREFERENCES, type Preferences } from "../api/types";
import { Button, Card, ErrorBanner, Field, SectionTitle, inputClass } from "../components/ui";

function listToText(values: string[]): string {
  return values.join(", ");
}

function textToList(text: string): string[] {
  return text
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
}

export function Settings() {
  const queryClient = useQueryClient();
  const preferencesQuery = useQuery({ queryKey: ["preferences"], queryFn: getPreferences });
  const [form, setForm] = useState<Preferences>(EMPTY_PREFERENCES);

  useEffect(() => {
    if (preferencesQuery.data) {
      setForm(preferencesQuery.data);
    }
  }, [preferencesQuery.data]);

  const saveMutation = useMutation({
    mutationFn: () => updatePreferences(form),
    onSuccess: (data) => {
      queryClient.setQueryData(["preferences"], data);
    },
  });

  const telegramStatusQuery = useQuery({
    queryKey: ["telegram-status"],
    queryFn: getTelegramStatus,
  });
  const [botToken, setBotToken] = useState("");
  const [chatId, setChatId] = useState("");
  const connectMutation = useMutation({
    mutationFn: () => connectTelegram(botToken, chatId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["telegram-status"] }),
  });
  const testMutation = useMutation({ mutationFn: testTelegram });

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <SectionTitle>Preferences</SectionTitle>

        <p className="mb-2 text-xs font-semibold tracking-wide text-slate-400 uppercase">
          Compensation &amp; experience
        </p>
        <div className="grid grid-cols-2 gap-4">
          <Field label="Desired salary (USD)">
            <input
              type="number"
              className={inputClass}
              value={form.desired_salary_usd ?? ""}
              onChange={(e) =>
                setForm({
                  ...form,
                  desired_salary_usd: e.target.value ? Number(e.target.value) : null,
                })
              }
            />
          </Field>
          <Field label="Max required experience (years)">
            <input
              type="number"
              className={inputClass}
              value={form.max_required_experience ?? ""}
              onChange={(e) =>
                setForm({
                  ...form,
                  max_required_experience: e.target.value ? Number(e.target.value) : null,
                })
              }
            />
          </Field>
        </div>

        <p className="mt-5 mb-2 text-xs font-semibold tracking-wide text-slate-400 uppercase">
          Role &amp; location
        </p>
        <div className="grid grid-cols-2 gap-4">
          <Field label="Preferred roles (comma-separated)">
            <input
              className={inputClass}
              value={listToText(form.preferred_roles)}
              onChange={(e) => setForm({ ...form, preferred_roles: textToList(e.target.value) })}
            />
          </Field>
          <Field label="Locations (comma-separated)">
            <input
              className={inputClass}
              value={listToText(form.locations)}
              onChange={(e) => setForm({ ...form, locations: textToList(e.target.value) })}
            />
          </Field>
          <Field label="Work formats (e.g. remote)">
            <input
              className={inputClass}
              value={listToText(form.work_formats)}
              onChange={(e) => setForm({ ...form, work_formats: textToList(e.target.value) })}
            />
          </Field>
        </div>

        <p className="mt-5 mb-2 text-xs font-semibold tracking-wide text-slate-400 uppercase">
          Tech stack
        </p>
        <div className="grid grid-cols-2 gap-4">
          <Field label="Preferred stack">
            <input
              className={inputClass}
              value={listToText(form.preferred_stack)}
              onChange={(e) => setForm({ ...form, preferred_stack: textToList(e.target.value) })}
            />
          </Field>
          <Field label="Acceptable stack">
            <input
              className={inputClass}
              value={listToText(form.acceptable_stack)}
              onChange={(e) => setForm({ ...form, acceptable_stack: textToList(e.target.value) })}
            />
          </Field>
          <Field label="Blocked stack">
            <input
              className={inputClass}
              value={listToText(form.blocked_stack)}
              onChange={(e) => setForm({ ...form, blocked_stack: textToList(e.target.value) })}
            />
          </Field>
        </div>

        <p className="mt-5 mb-2 text-xs font-semibold tracking-wide text-slate-400 uppercase">
          Exclusions
        </p>
        <div className="grid grid-cols-2 gap-4">
          <Field label="Companies blacklist">
            <input
              className={inputClass}
              value={listToText(form.companies_blacklist)}
              onChange={(e) =>
                setForm({ ...form, companies_blacklist: textToList(e.target.value) })
              }
            />
          </Field>
          <Field label="Industries blacklist">
            <input
              className={inputClass}
              value={listToText(form.industries_blacklist)}
              onChange={(e) =>
                setForm({ ...form, industries_blacklist: textToList(e.target.value) })
              }
            />
          </Field>
        </div>

        <div className="mt-4">
          <Button onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>
            {saveMutation.isPending ? "Saving…" : "Save preferences"}
          </Button>
          {saveMutation.isSuccess && (
            <span className="ml-3 text-sm text-green-700">Saved.</span>
          )}
          {saveMutation.isError && (
            <div className="mt-2">
              <ErrorBanner
                message={
                  saveMutation.error instanceof ApiError
                    ? saveMutation.error.message
                    : "Save failed"
                }
              />
            </div>
          )}
        </div>
      </Card>

      <Card>
        <div className="mb-3 flex items-center gap-3">
          <SectionTitle>Telegram</SectionTitle>
          {telegramStatusQuery.data && (
            <span
              className={`mb-3 rounded-full px-2.5 py-0.5 text-xs font-medium ${
                telegramStatusQuery.data.connected
                  ? "bg-green-100 text-green-800"
                  : "bg-slate-100 text-slate-600"
              }`}
            >
              {telegramStatusQuery.data.connected ? "Connected" : "Not connected"}
            </span>
          )}
        </div>
        <p className="mb-3 text-sm text-slate-600">
          Create a bot via @BotFather in Telegram to get a token, then message it once to get
          your chat id.
        </p>
        <div className="grid grid-cols-2 gap-4">
          <Field label="Bot token">
            <input
              className={inputClass}
              value={botToken}
              onChange={(e) => setBotToken(e.target.value)}
              placeholder="123456:AAExampleTokenGoesHere"
            />
          </Field>
          <Field label="Chat id">
            <input
              className={inputClass}
              value={chatId}
              onChange={(e) => setChatId(e.target.value)}
              placeholder="123456789"
            />
          </Field>
        </div>
        <div className="mt-4 flex items-center gap-3">
          <Button
            onClick={() => connectMutation.mutate()}
            disabled={connectMutation.isPending || !botToken || !chatId}
          >
            {connectMutation.isPending ? "Connecting…" : "Connect"}
          </Button>
          <Button
            onClick={() => testMutation.mutate()}
            disabled={testMutation.isPending}
            className="bg-slate-600 hover:bg-slate-500"
          >
            {testMutation.isPending ? "Sending…" : "Send test message"}
          </Button>
        </div>
        {connectMutation.isSuccess && (
          <p className="mt-2 text-sm text-green-700">
            Connected{connectMutation.data.bot_username && ` as @${connectMutation.data.bot_username}`}.
          </p>
        )}
        {connectMutation.isError && (
          <div className="mt-2">
            <ErrorBanner
              message={
                connectMutation.error instanceof ApiError
                  ? connectMutation.error.message
                  : "Connect failed"
              }
            />
          </div>
        )}
        {testMutation.isSuccess && (
          <p className="mt-2 text-sm text-green-700">Test message sent — check Telegram.</p>
        )}
        {testMutation.isError && (
          <div className="mt-2">
            <ErrorBanner
              message={
                testMutation.error instanceof ApiError ? testMutation.error.message : "Test failed"
              }
            />
          </div>
        )}
      </Card>
    </div>
  );
}
