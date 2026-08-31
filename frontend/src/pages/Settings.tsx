import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ApiError } from "../api/client";
import {
  connectTelegram,
  getNotificationThresholds,
  getPreferences,
  getTelegramBotInfo,
  getTelegramStatus,
  testTelegram,
  updateNotificationThresholds,
  updatePreferences,
} from "../api/endpoints";
import { EMPTY_PREFERENCES, type NotificationThresholds, type Preferences } from "../api/types";
import { Button, Card, ErrorBanner, Field, SectionTitle, inputClass } from "../components/ui";

const DEFAULT_THRESHOLDS: NotificationThresholds = {
  immediate_threshold: 85,
  conditional_threshold: 75,
  digest_threshold: 65,
  strong_component_threshold: 90,
  quiet_hours_start: 22,
  quiet_hours_end: 8,
};

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

  const notificationThresholdsQuery = useQuery({
    queryKey: ["notification-thresholds"],
    queryFn: getNotificationThresholds,
  });
  const [thresholds, setThresholds] = useState<NotificationThresholds>(DEFAULT_THRESHOLDS);

  useEffect(() => {
    if (notificationThresholdsQuery.data) {
      setThresholds(notificationThresholdsQuery.data);
    }
  }, [notificationThresholdsQuery.data]);

  const saveThresholdsMutation = useMutation({
    mutationFn: () => updateNotificationThresholds(thresholds),
    onSuccess: (data) => {
      queryClient.setQueryData(["notification-thresholds"], data);
    },
  });

  const telegramStatusQuery = useQuery({
    queryKey: ["telegram-status"],
    queryFn: getTelegramStatus,
  });
  const botInfoQuery = useQuery({
    queryKey: ["telegram-bot-info"],
    queryFn: getTelegramBotInfo,
  });
  const [chatId, setChatId] = useState("");
  const connectMutation = useMutation({
    mutationFn: () => connectTelegram(chatId),
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
        <SectionTitle>Notification thresholds</SectionTitle>
        <p className="mb-4 text-sm text-slate-600">
          Controls when a scored match gets sent to Telegram instantly, folded into the (not yet
          built) daily digest, or skipped entirely. See docs/notifications.md for the full policy.
        </p>
        <div className="grid grid-cols-2 gap-4">
          <Field label="Instant notification threshold (practical fit %)">
            <input
              type="number"
              min={0}
              max={100}
              className={inputClass}
              value={thresholds.immediate_threshold}
              onChange={(e) =>
                setThresholds({ ...thresholds, immediate_threshold: Number(e.target.value) })
              }
            />
          </Field>
          <Field label="Conditional threshold (%, needs strong salary + location too)">
            <input
              type="number"
              min={0}
              max={100}
              className={inputClass}
              value={thresholds.conditional_threshold}
              onChange={(e) =>
                setThresholds({ ...thresholds, conditional_threshold: Number(e.target.value) })
              }
            />
          </Field>
          <Field label="Strong salary/location match bar (%)">
            <input
              type="number"
              min={0}
              max={100}
              className={inputClass}
              value={thresholds.strong_component_threshold}
              onChange={(e) =>
                setThresholds({
                  ...thresholds,
                  strong_component_threshold: Number(e.target.value),
                })
              }
            />
          </Field>
          <Field label="Digest-only threshold (%, below this: no notification at all)">
            <input
              type="number"
              min={0}
              max={100}
              className={inputClass}
              value={thresholds.digest_threshold}
              onChange={(e) =>
                setThresholds({ ...thresholds, digest_threshold: Number(e.target.value) })
              }
            />
          </Field>
          <Field label="Quiet hours start (0-23, server time)">
            <input
              type="number"
              min={0}
              max={23}
              className={inputClass}
              value={thresholds.quiet_hours_start}
              onChange={(e) =>
                setThresholds({ ...thresholds, quiet_hours_start: Number(e.target.value) })
              }
            />
          </Field>
          <Field label="Quiet hours end (0-23, server time)">
            <input
              type="number"
              min={0}
              max={23}
              className={inputClass}
              value={thresholds.quiet_hours_end}
              onChange={(e) =>
                setThresholds({ ...thresholds, quiet_hours_end: Number(e.target.value) })
              }
            />
          </Field>
        </div>
        <div className="mt-4">
          <Button
            onClick={() => saveThresholdsMutation.mutate()}
            disabled={saveThresholdsMutation.isPending}
          >
            {saveThresholdsMutation.isPending ? "Saving…" : "Save thresholds"}
          </Button>
          {saveThresholdsMutation.isSuccess && (
            <span className="ml-3 text-sm text-green-700">Saved.</span>
          )}
          {saveThresholdsMutation.isError && (
            <div className="mt-2">
              <ErrorBanner
                message={
                  saveThresholdsMutation.error instanceof ApiError
                    ? saveThresholdsMutation.error.message
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
          {botInfoQuery.data?.username ? (
            <>
              Message{" "}
              <a
                href={`https://t.me/${botInfoQuery.data.username}`}
                target="_blank"
                rel="noreferrer"
                className="font-medium text-slate-900 underline"
              >
                @{botInfoQuery.data.username}
              </a>{" "}
              on Telegram to get your chat id, then paste it below.
            </>
          ) : (
            "No Telegram bot is configured on the server yet."
          )}
        </p>
        <div className="grid grid-cols-2 gap-4">
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
            disabled={connectMutation.isPending || !chatId}
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
