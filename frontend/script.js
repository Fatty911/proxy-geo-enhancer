document.addEventListener('DOMContentLoaded', () => {
    const subUrlsTextarea = document.getElementById('subUrls');
    const outputFormatSelect = document.getElementById('outputFormat');
    const processButton = document.getElementById('processButton');
    const loadingDiv = document.getElementById('loading');
    const resultAreaDiv = document.getElementById('resultArea');
    const newSubContentTextarea = document.getElementById('newSubContent');
    const newSubUrlLink = document.getElementById('newSubUrl');

    // 构建后端API的URL，根据实际部署情况修改
    // 如果前端和后端部署在同一个域名下，可以使用相对路径
    // const API_BASE_URL = 'http://aaaaa.com/api'; // 或者你的Docker服务URL
    const API_BASE_URL = '/api'; // 假设部署在同一域名，后端路径为 /api

    processButton.addEventListener('click', async () => {
        const urls = subUrlsTextarea.value.trim().split('\n').filter(url => url.trim() !== '');
        const outputFormat = outputFormatSelect.value;

        if (urls.length === 0) {
            alert('请输入至少一个订阅链接！');
            return;
        }

        loadingDiv.style.display = 'block';
        resultAreaDiv.style.display = 'none';
        processButton.disabled = true;
        newSubContentTextarea.value = '';
        newSubUrlLink.href = '#';
        newSubUrlLink.textContent = '';


        try {
            const response = await fetch(`${API_BASE_URL}/process-subscriptions`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    urls: urls,
                    output_format: outputFormat,
                }),
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || `HTTP error! status: ${response.status}`);
            }

            const result = await response.json();

            if (result.new_subscription_content) {
                newSubContentTextarea.value = result.new_subscription_content;
                resultAreaDiv.style.display = 'block';

                // 可选：如果后端能提供一个托管此内容的链接
                if(result.new_subscription_url) {
                     newSubUrlLink.href = result.new_subscription_url;
                     newSubUrlLink.textContent = result.new_subscription_url;
                } else {
                    // 生成一个data URI供用户复制 (对于非常大的订阅内容可能不理想)
                    let dataUriPrefix;
                    if (outputFormat === 'clash') {
                        dataUriPrefix = 'data:text/yaml;charset=utf-8,';
                    } else if (outputFormat === 'singbox') {
                        dataUriPrefix = 'data:application/json;charset=utf-8,';
                    } else {
                        dataUriPrefix = 'data:text/plain;charset=utf-8,';
                    }
                    const dataUri = dataUriPrefix + encodeURIComponent(result.new_subscription_content);
                    newSubUrlLink.href = dataUri;
                    newSubUrlLink.textContent = "复制此Data URI作为订阅链接 (右键复制链接)";
                }


            } else {
                throw new Error('未能获取新的订阅内容。');
            }

        } catch (error) {
            console.error('Error processing subscriptions:', error);
            alert(`处理失败: ${error.message}`);
            newSubContentTextarea.value = `处理失败: ${error.message}`;
            resultAreaDiv.style.display = 'block'; // 显示错误信息
        } finally {
            loadingDiv.style.display = 'none';
            processButton.disabled = false;
        }
    });
});