// The module 'vscode' contains the VS Code extensibility API
// Import the module and reference it with the alias vscode in your code below
const vscode = require('vscode');
const path = require('path');
const fs = require('fs');
const http = require('http');
const https = require('https');
const { exec } = require('child_process');

// This method is called when your extension is activated
// Your extension is activated the very first time the command is executed

/**
 * @param {vscode.ExtensionContext} context
 */
function activate(context) {
	try {
		// Use the console to output diagnostic information (console.log) and errors (console.error)
		// This line of code will only be executed once when your extension is activated
		console.log('DecisionsAI Tickets Watcher is now active!');

		// Create output channel for extension console
		// View logs: View > Output > Select "DecisionsAI" from dropdown
		const outputChannel = vscode.window.createOutputChannel('DecisionsAI');
		outputChannel.show(true);
		outputChannel.appendLine('DecisionsAI extension activated');
		outputChannel.appendLine('Logs will appear here. View: View > Output > DecisionsAI');

		// Register command to manually activate the ticket watcher
		const disposable = vscode.commands.registerCommand('decisionsai.activateTicketWatcher', function () {
			try {
				vscode.window.showInformationMessage('DecisionsAI Tickets Watcher is active!');
			} catch (error) {
				console.error('Error executing decisionsai.activateTicketWatcher:', error);
				vscode.window.showErrorMessage(`DecisionsAI Error: ${error.message}`);
			}
		});

		context.subscriptions.push(disposable);

		// Start watching for STARTUP.md file
		startStartupWatcher(outputChannel, context);

		// Start watching for files in .tickets/ folder
		startTicketWatcher(outputChannel, context);
	} catch (error) {
		console.error('Error activating DecisionsAI extension:', error);
		vscode.window.showErrorMessage(`Failed to activate DecisionsAI extension: ${error.message}`);
	}
}

/**
 * Check if a file is an image based on extension
 * @param {string} filename
 * @returns {boolean}
 */
function isImageFile(filename) {
	const imageExtensions = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.ico'];
	const ext = path.extname(filename).toLowerCase();
	return imageExtensions.includes(ext);
}

async function submitTicketContents(text, imagePaths, outputChannel) {
	await submitViaChatInput(text, imagePaths, outputChannel);
}

/**
 * Send Enter key via AppleScript with retry logic
 * @param {vscode.OutputChannel} outputChannel
 * @param {string} context Description of what this Enter is for (logging)
 * @returns {Promise<boolean>} True if Enter was sent successfully
 */
async function sendEnterKey(outputChannel, context = 'message') {
	let enterSuccess = false;
	for (let attempt = 1; attempt <= 3; attempt++) {
		await new Promise((resolve) => {
			exec(`osascript -e 'tell application "System Events" to key code 36'`, (error) => {
				if (!error) {
					enterSuccess = true;
					outputChannel.appendLine(`✓ Enter key sent for ${context} (attempt ${attempt})`);
				} else {
					outputChannel.appendLine(`⚠ Enter attempt ${attempt} failed: ${error.message}`);
				}
				resolve();
			});
		});

		if (enterSuccess) break;
		await new Promise(resolve => setTimeout(resolve, 500));
	}
	return enterSuccess;
}

/**
 * Insert text and images into Cursor's agent input textbox
 * Simple flow: Focus existing chat -> Paste -> Submit
 * @param {string} text The text to insert
 * @param {string[]} imagePaths Array of image file paths to attach
 * @param {vscode.OutputChannel} outputChannel
 */
async function submitViaChatInput(text, imagePaths, outputChannel) {
	try {
		// Build the full message with image references
		let fullMessage = text;

		// Add @file references for images at the end
		if (imagePaths && imagePaths.length > 0) {
			outputChannel.appendLine(`Adding ${imagePaths.length} image reference(s) to message...`);
			const imageRefs = imagePaths.map(p => `@${p}`).join(' ');
			fullMessage = `${text}\n\n${imageRefs}`;
			outputChannel.appendLine(`Image references: ${imageRefs}`);
		}

		outputChannel.appendLine('=== SUBMITTING TICKET ===');

		// Step 1: Focus the existing chat panel (don't create new)
		outputChannel.appendLine('Focusing chat panel...');
		try {
			await vscode.commands.executeCommand('workbench.action.chat.focus');
		} catch (e) {
			outputChannel.appendLine('Focus command not available, using open...');
			await vscode.commands.executeCommand('workbench.action.chat.open');
		}
		await new Promise(resolve => setTimeout(resolve, 800));

		// Step 2: Activate Cursor to ensure focus
		await new Promise((resolve) => {
			exec(`osascript -e 'tell application "Cursor" to activate'`, () => resolve());
		});
		await new Promise(resolve => setTimeout(resolve, 400));

		// Step 3: Paste the ticket content
		outputChannel.appendLine('Pasting ticket content...');
		await vscode.env.clipboard.writeText(fullMessage);
		await new Promise(resolve => setTimeout(resolve, 200));
		await vscode.commands.executeCommand('editor.action.clipboardPasteAction');
		await new Promise(resolve => setTimeout(resolve, 500));

		// Step 4: Submit with Enter
		outputChannel.appendLine('Submitting ticket...');
		const enterSuccess = await sendEnterKey(outputChannel, 'ticket submission');

		if (!enterSuccess) {
			vscode.window.showInformationMessage('DecisionsAI: Ticket pasted. Press Enter to submit.');
		}

		outputChannel.appendLine('=== TICKET SUBMITTED ===');

	} catch (error) {
		outputChannel.appendLine(`Error submitting ticket: ${error.message}`);
		console.error('Error submitting ticket:', error);
		vscode.window.showErrorMessage(`DecisionsAI Error: ${error.message}`);
	}
}

/**
 * Process STARTUP.md file - each line opens a new terminal and runs the command
 * @param {vscode.OutputChannel} outputChannel
 */
async function processStartupFile(outputChannel) {
	const workspaceFolders = vscode.workspace.workspaceFolders;
	if (!workspaceFolders || workspaceFolders.length === 0) {
		return;
	}

	const workspaceRoot = workspaceFolders[0].uri.fsPath;
	const ticketsFolder = path.join(workspaceRoot, '.tickets');
	const startupFilePath = path.join(ticketsFolder, 'STARTUP.md');

	// Check if STARTUP.md exists
	if (!fs.existsSync(startupFilePath)) {
		return;
	}

	outputChannel.appendLine('\n=== Processing STARTUP.md ===');

	try {
		// Read file contents
		const fileContents = fs.readFileSync(startupFilePath, 'utf8');
		const lines = fileContents.split('\n').filter(line => line.trim() !== '');

		if (lines.length === 0) {
			outputChannel.appendLine('STARTUP.md is empty. Deleting file.');
			fs.unlinkSync(startupFilePath);
			return;
		}

		outputChannel.appendLine(`Found ${lines.length} command(s) to execute:`);
		lines.forEach((line, i) => outputChannel.appendLine(`  ${i + 1}: ${line}`));

		// Delete the file BEFORE processing to prevent double processing
		fs.unlinkSync(startupFilePath);
		outputChannel.appendLine('Deleted STARTUP.md file.');

		// Execute each line in its own terminal
		for (let i = 0; i < lines.length; i++) {
			const command = lines[i].trim();
			if (!command) continue;

			const terminalName = `Startup ${i + 1}`;
			outputChannel.appendLine(`Opening terminal "${terminalName}" for: ${command}`);

			// Create a new terminal
			const terminal = vscode.window.createTerminal({
				name: terminalName,
				cwd: workspaceRoot
			});

			// Show the terminal
			terminal.show(false); // false = don't take focus

			// Send the command
			terminal.sendText(command);

			// Small delay between terminal creations to avoid overwhelming the system
			await new Promise(resolve => setTimeout(resolve, 500));
		}

		outputChannel.appendLine(`=== STARTUP.md Complete: ${lines.length} terminal(s) opened ===\n`);
		vscode.window.showInformationMessage(`DecisionsAI: Started ${lines.length} terminal(s) from STARTUP.md`);

	} catch (error) {
		outputChannel.appendLine(`Error processing STARTUP.md: ${error.message}`);
		console.error('Error processing STARTUP.md:', error);
	}
}

/**
 * Watch for STARTUP.md file in .tickets/ folder and process it
 * @param {vscode.OutputChannel} outputChannel
 * @param {vscode.ExtensionContext} context
 */
function startStartupWatcher(outputChannel, context) {
	// Process on activation
	processStartupFile(outputChannel);

	// Also watch for file creation
	const workspaceFolders = vscode.workspace.workspaceFolders;
	if (!workspaceFolders || workspaceFolders.length === 0) {
		return;
	}

	const workspaceRoot = workspaceFolders[0].uri.fsPath;
	const ticketsFolder = path.join(workspaceRoot, '.tickets');

	// Create file watcher for STARTUP.md in .tickets/ folder
	const watcher = vscode.workspace.createFileSystemWatcher(
		new vscode.RelativePattern(ticketsFolder, 'STARTUP.md'),
		false, // don't ignore creates
		true,  // ignore changes
		true   // ignore deletes
	);

	watcher.onDidCreate(() => {
		outputChannel.appendLine('STARTUP.md file detected in .tickets/!');
		// Small delay to ensure file is fully written
		setTimeout(() => processStartupFile(outputChannel), 500);
	});

	context.subscriptions.push(watcher);
	outputChannel.appendLine('STARTUP.md watcher active (watching .tickets/ folder)');
}

/**
 * Parse decisions-meta comment from ticket file contents.
 * Format: <!-- decisions-meta: {"run_id": 42, "step_id": 7, "workflow_id": 5, "api_base": "http://localhost:5555"} -->
 * @param {string} fileContents
 * @returns {{ run_id: number, step_id: number, workflow_id: number, api_base: string, callback_url?: string, callback_payload_type?: string, context_type?: string } | null}
 */
function parseDecisionsMeta(fileContents) {
	try {
		const match = fileContents.match(/<!--\s*decisions-meta:\s*(\{.*?\})\s*-->/);
		if (!match) return null;
		const meta = JSON.parse(match[1]);
		if (meta.run_id != null && meta.api_base) {
			return meta;
		}
		return null;
	} catch (e) {
		return null;
	}
}

/**
 * Parse optional ticket dispatch controls from YAML frontmatter.
 * Supported controls:
 * - mode: append
 * - append_to_current_session: true
 * - continue_current_ticket: true
 * - do_not_start_new_ticket: true
 * @param {string} fileContents
 * @returns {{ appendToCurrentSession: boolean, body: string }}
 */
function parseTicketDispatchControl(fileContents) {
	try {
		const frontmatterMatch = fileContents.match(/^---\s*\n([\s\S]*?)\n---\s*\n?/);
		if (!frontmatterMatch) {
			return { appendToCurrentSession: false, body: fileContents };
		}

		const rawFrontmatter = frontmatterMatch[1] || '';
		const body = fileContents.slice(frontmatterMatch[0].length);
		const controlMap = {};

		rawFrontmatter.split('\n').forEach((line) => {
			const trimmed = line.trim();
			if (!trimmed || trimmed.startsWith('#')) return;
			const idx = trimmed.indexOf(':');
			if (idx <= 0) return;
			const key = trimmed.slice(0, idx).trim().toLowerCase();
			const value = trimmed.slice(idx + 1).trim().toLowerCase();
			controlMap[key] = value;
		});

		const isTrue = (v) => ['true', '1', 'yes', 'y', 'on'].includes(String(v || '').toLowerCase());
		const appendToCurrentSession =
			(controlMap.mode === 'append') ||
			isTrue(controlMap.append_to_current_session) ||
			isTrue(controlMap.continue_current_ticket) ||
			isTrue(controlMap.do_not_start_new_ticket);

		return { appendToCurrentSession, body: body || fileContents };
	} catch (e) {
		return { appendToCurrentSession: false, body: fileContents };
	}
}

/**
 * Post callback payload to explicit callback_url if present.
 * Falls back to workflow continue endpoint for legacy metadata.
 * @param {{ run_id: number, step_id: number, workflow_id: number, api_base: string, callback_url?: string, callback_payload_type?: string, context_type?: string }} meta
 * @param {{ event_type: string, status: string, input: string, filename: string }} payload
 * @param {vscode.OutputChannel} outputChannel
 */
function callTicketCallback(meta, payload, outputChannel) {
	try {
		const wfId = meta.workflow_id || 0;
		const fallbackUrl = `${meta.api_base}/api/workflows/${wfId}/runs/${meta.run_id}/continue`;
		const url = meta.callback_url || fallbackUrl;
		const callbackType = (meta.callback_payload_type || '').toLowerCase();
		const bodyObj = (callbackType === 'workflow_continue')
			? { input: payload.input }
			: {
				event_type: payload.event_type,
				status: payload.status,
				context_type: meta.context_type || 'unknown',
				run_id: meta.run_id || null,
				step_id: meta.step_id || null,
				workflow_id: meta.workflow_id || null,
				filename: payload.filename || '',
				input: payload.input || '',
				ts: new Date().toISOString(),
			};
		const body = JSON.stringify(bodyObj);
		const parsedUrl = new URL(url);
		const transport = parsedUrl.protocol === 'https:' ? https : http;

		const options = {
			hostname: parsedUrl.hostname,
			port: parsedUrl.port,
			path: parsedUrl.pathname,
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',
				'Content-Length': Buffer.byteLength(body)
			}
		};

		const req = transport.request(options, (res) => {
			let data = '';
			res.on('data', (chunk) => { data += chunk; });
			res.on('end', () => {
				outputChannel.appendLine(`Callback response (${res.statusCode}): ${data}`);
			});
		});

		req.on('error', (err) => {
			outputChannel.appendLine(`⚠ Callback failed: ${err.message}`);
		});

		req.write(body);
		req.end();
		outputChannel.appendLine(`Sent callback to ${url}`);
	} catch (err) {
		outputChannel.appendLine(`⚠ Callback error: ${err.message}`);
	}
}

/**
 * Watch for files in .tickets/ folder and process them
 * @param {vscode.OutputChannel} outputChannel
 * @param {vscode.ExtensionContext} context
 */
function startTicketWatcher(outputChannel, context) {
	const workspaceFolders = vscode.workspace.workspaceFolders;
	if (!workspaceFolders || workspaceFolders.length === 0) {
		outputChannel.appendLine('No workspace folder found. Ticket watcher not started.');
		return;
	}

	const workspaceRoot = workspaceFolders[0].uri.fsPath;
	const ticketsFolder = path.join(workspaceRoot, '.tickets');

	outputChannel.appendLine(`Watching for files in: ${ticketsFolder}`);

	// Lock to prevent processing while already processing
	let isProcessing = false;

	// Check every 3 seconds for files
	const intervalId = setInterval(async () => {
		// Skip if already processing
		if (isProcessing) {
			return;
		}

		try {
			// Check if .tickets folder exists
			if (!fs.existsSync(ticketsFolder)) {
				return; // Folder doesn't exist yet, skip this check
			}

			// Read directory contents
			const files = fs.readdirSync(ticketsFolder);
			
			// Separate text files (.md and .txt) from image files
			const textFiles = [];
			const imageFiles = [];
			
			for (const filename of files) {
				const filePath = path.join(ticketsFolder, filename);
				const stat = fs.statSync(filePath);
				
				if (stat.isFile()) {
					const lowerName = filename.toLowerCase();
					// Skip STARTUP.md - it's handled by the startup watcher
					if (lowerName === 'startup.md') {
						continue;
					}
					if (lowerName.endsWith('.md') || lowerName.endsWith('.txt')) {
						textFiles.push({ filename, filePath });
					} else if (isImageFile(filename)) {
						imageFiles.push(filePath);
					}
				}
			}
			
			// Skip if no text files to process
			if (textFiles.length === 0) {
				return;
			}

			// Set processing lock
			isProcessing = true;

			// Process text files (.md and .txt) - only process the first one
			const { filename, filePath } = textFiles[0];
			try {
				// Read file contents
				const fileContents = fs.readFileSync(filePath, 'utf8');
				
				// Parse decisions-meta before deleting the file
				const decisionsMeta = parseDecisionsMeta(fileContents);
				if (decisionsMeta) {
					outputChannel.appendLine(`Found decisions-meta: run_id=${decisionsMeta.run_id}, step_id=${decisionsMeta.step_id}, api_base=${decisionsMeta.api_base}`);
				}

				// Parse dispatch controls to decide whether to append to current session
				const dispatchControl = parseTicketDispatchControl(fileContents);
				let ticketPayload = fileContents;
				if (dispatchControl.appendToCurrentSession) {
					outputChannel.appendLine('Dispatch mode: append to current running ticket/session');
					ticketPayload =
						'[APPEND MODE]\n' +
						'Continue the CURRENT running ticket/session. Do NOT start a new ticket, new task, or new session.\n\n' +
						(dispatchControl.body || '').trim();
				}
				
				// Log to extension console
				outputChannel.appendLine(`\n=== Processing Ticket File ===`);
				outputChannel.appendLine(`Filename: ${filename}`);
				outputChannel.appendLine(`Contents:\n${fileContents}`);
				outputChannel.appendLine(`=== End of File ===\n`);
				
				// Check if there are images in the folder to attach
				if (imageFiles.length > 0) {
					outputChannel.appendLine(`Found ${imageFiles.length} image(s) to attach: ${imageFiles.map(p => path.basename(p)).join(', ')}`);
				}
				
				// Delete the file BEFORE processing to prevent double processing
				fs.unlinkSync(filePath);
				outputChannel.appendLine(`Deleted ticket file: ${filename}`);
				
				// Insert ticket contents and images into agent input textbox
				await submitTicketContents(ticketPayload, imageFiles, outputChannel);
				
				outputChannel.appendLine('Chat submission completed.');
				
				// Post callback if metadata was present (workflow or custom callback URL).
				if (decisionsMeta) {
					callTicketCallback(decisionsMeta, {
						event_type: 'cursor_ticket_submitted',
						status: 'submitted',
						filename: filename,
						input: 'Cursor ticket submitted from VS extension',
					}, outputChannel);
				}
			} catch (error) {
				outputChannel.appendLine(`Error processing file ${filename}: ${error.message}`);
				console.error(`Error processing ticket file ${filename}:`, error);
			} finally {
				// Release processing lock
				isProcessing = false;
			}
		} catch (error) {
			// Silently handle errors (folder might not exist yet)
			if (error.code !== 'ENOENT') {
				outputChannel.appendLine(`Error checking tickets folder: ${error.message}`);
				console.error('Error checking tickets folder:', error);
			}
			isProcessing = false;
		}
	}, 3000); // Check every 3 seconds

	// Clean up interval on deactivation
	context.subscriptions.push({
		dispose: () => {
			clearInterval(intervalId);
			outputChannel.appendLine('Ticket watcher stopped');
		}
	});
}

// This method is called when your extension is deactivated
function deactivate() {}

module.exports = {
	activate,
	deactivate
}
