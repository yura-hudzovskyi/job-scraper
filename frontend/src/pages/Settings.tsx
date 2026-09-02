import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ApiError } from "../api/client";
import {
  connectTelegram,
  getNotificationSettings,
  getPreferences,
  getTelegramBotInfo,
  getTelegramStatus,
  testTelegram,
  updateNotificationSettings,
  updatePreferences,
} from "../api/endpoints";
import { EMPTY_PREFERENCES, type NotificationSettings, type Preferences } from "../api/types";
import {
  Badge,
  Button,
  Card,
  ErrorBanner,
  Field,
  InfoBanner,
  SecondaryButton,
  SectionTitle,
  inputClass,
} from "../components/ui";

const DEFAULT_NOTIFICATIONS: NotificationSettings = {
  enabled: true,
  min_score: 75,
  quiet_hours_start: 22,
  quiet_hours_end: 8,
};

const listToText = (values: string[]) => values.join(", ");
const textToList = (text: string) =>
  text
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);

export function Settings() {
  const queryClient = useQueryClient();

  const preferencesQuery = useQuery({ queryKey: ["preferences"], queryFn: getPreferences });
  const [form, setForm] = useState<Preferences>(EMPTY_PREFERENCES);
  useEffect(() => {
    if (preferencesQuery.data) setForm(preferencesQuery.data);
  }, [preferencesQuery.data]);

  const saveMutation = useMutation({
    mutationFn: () => updatePreferences(form),
    onSuccess: (data) => queryClient.setQueryData(["preferences"], data),
  });

  const notificationsQuery = useQuery({
    queryKey: ["notification-settings"],
    queryFn: getNotificationSettings,
  });
  const [notifications, setNotifications] = useState<NotificationSettings>(DEFAULT_NOTIFICATIONS);
  useEffect(() => {
    if (notificationsQuery.data) setNotifications(notificationsQuery.data);
  }, [notificationsQuery.data]);

  const saveNotificationsMutation = useMutation({
    mutationFn: () => updateNotificationSettings(notifications),
    onSuccess: (data) => queryClient.setQueryData(["notification-settings"], data),
  });

  const telegramStatusQuery = useQuery({
    queryKey: ["telegram-status"],
    queryFn: getTelegramStatus,
  });
  const botInfoQuery = useQuery({ queryKey: ["telegram-bot-info"], queryFn: getTelegramBotInfo });
  const [chatId, setChatId] = useState("");
  const connectMutation = useMutation({
    mutationFn: () => connectTelegram(chatId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["telegram-status"] }),
  });
  const testMutation = useMutation({ mutationFn: testTelegram });

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <SectionTitle>What you're looking for</SectionTitle>
        <p className="mb-4 text-sm text-slate-600">
          The first three fields describe what you want and go into the text the models read
          alongside your CV. Everything under "Rules" is a hard filter instead: it removes vacancies
          before any scoring, and every removal is shown with its reason on the job page.
        </p>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Preferred roles" hint="Comma-separated. Sent to the models as your target.">
            <input
              className={inputClass}
              value={listToText(form.preferred_roles)}
              onChange={(e) => setForm({ ...form, preferred_roles: textToList(e.target.value) })}
            />
          </Field>
          <Field label="Preferred stack" hint="What you want to be matched on, not a constraint.">
            <input
              className={inputClass}
              value={listToText(form.preferred_stack)}
              onChange={(e) => setForm({ ...form, preferred_stack: textToList(e.target.value) })}
            />
          </Field>
          <Field
            label="Work formats"
            hint='e.g. "remote". Listing only "remote" also filters out non-remote vacancies.'
          >
            <input
              className={inputClass}
              value={listToText(form.work_formats)}
              onChange={(e) => setForm({ ...form, work_formats: textToList(e.target.value) })}
            />
          </Field>
        </div>

        <p className="mt-6 mb-2 text-xs font-semibold tracking-wide text-slate-400 uppercase">
          Rules — these remove vacancies
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label="Minimum salary (USD)"
            hint="Rejects a vacancy only when it states a USD maximum below this. An unstated or non-USD salary never rejects."
          >
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
          <Field
            label="Max required experience (years)"
            hint="Rejects vacancies asking for more than this, when they say so."
          >
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
          <Field
            label="Blocked stack"
            hint="Rejects any vacancy whose title or description mentions one of these."
          >
            <input
              className={inputClass}
              value={listToText(form.blocked_stack)}
              onChange={(e) => setForm({ ...form, blocked_stack: textToList(e.target.value) })}
            />
          </Field>
          <Field
            label="Locations"
            hint="Rejects vacancies restricted to somewhere outside this list. Leave empty to allow anywhere."
          >
            <input
              className={inputClass}
              value={listToText(form.locations)}
              onChange={(e) => setForm({ ...form, locations: textToList(e.target.value) })}
            />
          </Field>
          <Field label="Companies blacklist" hint="Exact company-name match, case-insensitive.">
            <input
              className={inputClass}
              value={listToText(form.companies_blacklist)}
              onChange={(e) =>
                setForm({ ...form, companies_blacklist: textToList(e.target.value) })
              }
            />
          </Field>
        </div>

        <div className="mt-4 flex items-center gap-3">
          <Button onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>
            {saveMutation.isPending ? "Saving…" : "Save"}
          </Button>
          {saveMutation.isSuccess && (
            <span className="text-sm text-green-700">
              Saved — re-matching every vacancy in the background.
            </span>
          )}
        </div>
        {saveMutation.isError && (
          <div className="mt-2">
            <ErrorBanner
              message={
                saveMutation.error instanceof ApiError ? saveMutation.error.message : "Save failed"
              }
            />
          </div>
        )}
      </Card>

      <Card>
        <SectionTitle>Notifications</SectionTitle>
        <p className="mb-4 text-sm text-slate-600">
          A Telegram card is sent for each new match at or above this score, outside quiet hours.
          Delivery is recorded per match, so the same vacancy is never sent twice.
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={notifications.enabled}
              onChange={(e) => setNotifications({ ...notifications, enabled: e.target.checked })}
            />
            Send Telegram notifications
          </label>
          <Field label="Minimum score (0-100)">
            <input
              type="number"
              min={0}
              max={100}
              className={inputClass}
              value={notifications.min_score}
              onChange={(e) =>
                setNotifications({ ...notifications, min_score: Number(e.target.value) })
              }
            />
          </Field>
          <Field label="Quiet hours start (0-23, server time)">
            <input
              type="number"
              min={0}
              max={23}
              className={inputClass}
              value={notifications.quiet_hours_start}
              onChange={(e) =>
                setNotifications({ ...notifications, quiet_hours_start: Number(e.target.value) })
              }
            />
          </Field>
          <Field
            label="Quiet hours end (0-23)"
            hint="Set both to the same value for no quiet window at all."
          >
            <input
              type="number"
              min={0}
              max={23}
              className={inputClass}
              value={notifications.quiet_hours_end}
              onChange={(e) =>
                setNotifications({ ...notifications, quiet_hours_end: Number(e.target.value) })
              }
            />
          </Field>
        </div>
        <div className="mt-4 flex items-center gap-3">
          <Button
            onClick={() => saveNotificationsMutation.mutate()}
            disabled={saveNotificationsMutation.isPending}
          >
            {saveNotificationsMutation.isPending ? "Saving…" : "Save"}
          </Button>
          {saveNotificationsMutation.isSuccess && (
            <span className="text-sm text-green-700">Saved.</span>
          )}
        </div>
      </Card>

      <Card>
        <div className="mb-3 flex items-center gap-3">
          <SectionTitle>Telegram</SectionTitle>
          {telegramStatusQuery.data && (
            <span className="mb-3">
              <Badge tone={telegramStatusQuery.data.connected ? "ok" : "neutral"}>
                {telegramStatusQuery.data.connected ? "Connected" : "Not connected"}
              </Badge>
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
            "No Telegram bot is configured on the server (TELEGRAM_BOT_TOKEN is unset)."
          )}
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
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
          <SecondaryButton onClick={() => testMutation.mutate()} disabled={testMutation.isPending}>
            {testMutation.isPending ? "Sending…" : "Send test message"}
          </SecondaryButton>
        </div>
        {connectMutation.isSuccess && (
          <div className="mt-3">
            <InfoBanner tone="ok">
              Connected
              {connectMutation.data.bot_username && ` as @${connectMutation.data.bot_username}`}.
            </InfoBanner>
          </div>
        )}
        {testMutation.isSuccess && (
          <p className="mt-2 text-sm text-green-700">Test message sent — check Telegram.</p>
        )}
        {(connectMutation.isError || testMutation.isError) && (
          <div className="mt-2">
            <ErrorBanner
              message={
                (connectMutation.error ?? testMutation.error) instanceof ApiError
                  ? (connectMutation.error ?? testMutation.error)!.message
                  : "Telegram request failed"
              }
            />
          </div>
        )}
      </Card>
    </div>
  );
}
